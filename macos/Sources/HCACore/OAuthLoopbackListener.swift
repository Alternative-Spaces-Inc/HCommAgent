import Foundation
import Network

/// Owns all listener state on one actor. Unrelated local traffic cannot complete OAuth.
public actor OAuthLoopbackListener {
    public enum ListenerError: Error { case listenFailed }
    private var listener: NWListener?
    private var ready: CheckedContinuation<UInt16, Error>?
    private var continuation: AsyncThrowingStream<String, Error>.Continuation?
    private var connections: [UUID: NWConnection] = [:]
    private var tasks: [UUID: Task<Void, Never>] = [:]
    private var readyDeadline: Task<Void, Never>?
    private let timeoutNanoseconds: UInt64
    private let queue = DispatchQueue(label: "hca.oauth.loopback")
    public static let maximumConnections = 8
    public static let connectionTimeoutNanoseconds: UInt64 = 5_000_000_000
    public init(connectionTimeoutNanoseconds: UInt64 = OAuthLoopbackListener.connectionTimeoutNanoseconds) {
        timeoutNanoseconds = connectionTimeoutNanoseconds
    }

    public func start(expectedState: String) async throws -> (UInt16, AsyncThrowingStream<String, Error>) {
        guard listener == nil else { throw ListenerError.listenFailed }
        let params = NWParameters.tcp
        params.requiredInterfaceType = .loopback
        params.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        let listener = try NWListener(using: params)
        self.listener = listener
        let (stream, continuation) = AsyncThrowingStream<String, Error>.makeStream()
        self.continuation = continuation
        continuation.onTermination = { [weak self] _ in Task { await self?.stop() } }
        listener.newConnectionHandler = { [weak self] connection in
            Task { await self?.accept(connection, expectedState: expectedState) }
        }
        listener.stateUpdateHandler = { [weak self] state in
            Task { await self?.stateChanged(state) }
        }
        do {
            let port = try await withTaskCancellationHandler {
                try await withCheckedThrowingContinuation { ready in
                    self.ready = ready
                    self.readyDeadline = Task {
                        do { try await Task.sleep(nanoseconds: 5_000_000_000); self.stop(error: ListenerError.listenFailed) }
                        catch { }
                    }
                    listener.start(queue: queue)
                }
            } onCancel: { Task { await self.stop() } }
            try Task.checkCancellation()
            return (port, stream)
        } catch { stop(); throw error }
    }

    private func stateChanged(_ state: NWListener.State) {
        switch state {
        case .ready:
            guard let port = listener?.port?.rawValue, port != 0 else { stop(); return }
            readyDeadline?.cancel(); readyDeadline = nil
            let current = ready; ready = nil; current?.resume(returning: port)
        case .failed, .cancelled: stop(error: ListenerError.listenFailed)
        default: break
        }
    }

    private func accept(_ connection: NWConnection, expectedState: String) {
        guard listener != nil, connections.count < Self.maximumConnections else { connection.cancel(); return }
        let id = UUID()
        connections[id] = connection
        connection.start(queue: queue)
        tasks[id] = Task { await serve(connection, id: id, expectedState: expectedState) }
    }

    private func serve(_ connection: NWConnection, id: UUID, expectedState: String) async {
        let deadline = Task {
            do { try await Task.sleep(nanoseconds: timeoutNanoseconds); connection.cancel() }
            catch { /* cancelled when the bounded request completed */ }
        }
        defer {
            deadline.cancel(); connection.cancel()
            connections.removeValue(forKey: id); tasks.removeValue(forKey: id)
        }
        var line = LoopbackRequestLine()
        do {
            while !Task.isCancelled {
                let (data, complete) = try await withCheckedThrowingContinuation { (c: CheckedContinuation<(Data, Bool), Error>) in
                    connection.receive(minimumIncompleteLength: 1, maximumLength: 1024) { data, _, complete, error in
                        if let error { c.resume(throwing: error) }
                        else { c.resume(returning: (data ?? Data(), complete)) }
                    }
                }
                switch line.append(data, complete: complete) {
                case .pending: continue
                case .rejected: return
                case .line(let requestLine):
                    let result = GoogleOAuth.parseRedirect(requestLine: requestLine, expectedState: expectedState)
                    let ok: Bool
                    switch result { case .code: ok = true; default: ok = false }
                    let html = GoogleOAuth.responseHTML(ok: ok)
                    let status = ok ? "200 OK" : "400 Bad Request"
                    let head = "HTTP/1.1 \(status)\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: \(html.utf8.count)\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n"
                    await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                        connection.send(content: Data((head + html).utf8), completion: .contentProcessed { _ in c.resume() })
                    }
                    switch result {
                    case .code(let code): continuation?.yield(code); continuation?.finish()
                    case .error(let error): continuation?.finish(throwing: GoogleOAuth.CallbackError.denied(error))
                    case .stateMismatch, .notARedirect: break
                    }
                    return
                }
            }
        } catch { /* Ignore invalid/incomplete connections; keep the legitimate OAuth flow alive. */ }
    }

    public func stop(error: Error? = nil) {
        readyDeadline?.cancel(); readyDeadline = nil
        let current = ready; ready = nil
        current?.resume(throwing: error ?? CancellationError())
        let stream = continuation; continuation = nil
        if let error { stream?.finish(throwing: error) } else { stream?.finish() }
        listener?.cancel(); listener = nil
        for task in tasks.values { task.cancel() }
        for connection in connections.values { connection.cancel() }
        tasks.removeAll(); connections.removeAll()
    }
}
