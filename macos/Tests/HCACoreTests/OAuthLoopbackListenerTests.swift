import Darwin
import Foundation
import XCTest
@testable import HCACore

private enum LoopbackSocket {
    enum SocketError: Error { case failed(Int32) }
    static func connect(port: UInt16) throws -> Int32 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw SocketError.failed(errno) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = port.bigEndian
        address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        let status = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        guard status == 0 else { close(fd); throw SocketError.failed(errno) }
        var timeout = timeval(tv_sec: 2, tv_usec: 0)
        _ = withUnsafePointer(to: &timeout) { setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, $0, socklen_t(MemoryLayout<timeval>.size)) }
        var noSIGPIPE: Int32 = 1
        _ = withUnsafePointer(to: &noSIGPIPE) { setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, $0, socklen_t(MemoryLayout<Int32>.size)) }
        return fd
    }
    static func transact(port: UInt16, fragments: [String]) throws -> String {
        let fd = try connect(port: port)
        defer { close(fd) }
        for fragment in fragments {
            let bytes = Data(fragment.utf8)
            let count = bytes.withUnsafeBytes { send(fd, $0.baseAddress, $0.count, 0) }
            guard count == bytes.count else { throw SocketError.failed(errno) }
            usleep(10_000) // Deliberately separate TCP receives.
        }
        var bytes = [UInt8](repeating: 0, count: 16384)
        let count = recv(fd, &bytes, bytes.count, 0)
        guard count >= 0 else { throw SocketError.failed(errno) }
        return String(decoding: bytes.prefix(count), as: UTF8.self)
    }
}

final class OAuthLoopbackListenerTests: XCTestCase {
    func testUnsolicitedErrorThenFragmentedValidCallback() async throws {
        let listener = OAuthLoopbackListener()
        let (port, stream) = try await listener.start(expectedState: "trusted")
        let unrelated = try await Task.detached {
            try LoopbackSocket.transact(port: port, fragments: ["GET /oauth2?state=evil&error=access_denied HTTP/1.1\r\n"])
        }.value
        XCTAssertTrue(unrelated.hasPrefix("HTTP/1.1 400"))
        let valid = try await Task.detached {
            try LoopbackSocket.transact(port: port, fragments: ["GET /oauth2?sta", "te=trusted&co", "de=4%2Fcode HTTP/1.1\r", "\nHost: localhost\r\n\r\n"])
        }.value
        XCTAssertTrue(valid.hasPrefix("HTTP/1.1 200"))
        var iterator = stream.makeAsyncIterator()
        let code = try await iterator.next()
        XCTAssertEqual(code, "4/code")
        await listener.stop()
    }

    func testSlowIncompleteConnectionTimesOutWithoutAbortingFlow() async throws {
        let listener = OAuthLoopbackListener(connectionTimeoutNanoseconds: 100_000_000)
        let (port, stream) = try await listener.start(expectedState: "trusted")
        let incomplete = try await Task.detached {
            try LoopbackSocket.transact(port: port, fragments: ["GET /oauth2?state=trusted"])
        }.value
        XCTAssertEqual(incomplete, "")
        let valid = try await Task.detached {
            try LoopbackSocket.transact(port: port, fragments: ["GET /oauth2?state=trusted&code=ok HTTP/1.1\r\n"])
        }.value
        XCTAssertTrue(valid.hasPrefix("HTTP/1.1 200"))
        var iterator = stream.makeAsyncIterator()
        let code = try await iterator.next()
        XCTAssertEqual(code, "ok")
        await listener.stop()
    }

    func testCancellationClosesListenerAndWaitingStream() async throws {
        let listener = OAuthLoopbackListener()
        let (_, stream) = try await listener.start(expectedState: "trusted")
        let waiting = Task { () throws -> String? in
            var iterator = stream.makeAsyncIterator()
            return try await iterator.next()
        }
        waiting.cancel()
        let result = try await waiting.value
        XCTAssertNil(result)
        await listener.stop()
    }

    func testAuthenticatedErrorCompletesTheFlow() async throws {
        let listener = OAuthLoopbackListener()
        let (port, stream) = try await listener.start(expectedState: "trusted")
        _ = try await Task.detached {
            try LoopbackSocket.transact(port: port, fragments: ["GET /oauth2?state=trusted&error=access_denied HTTP/1.1\r\n"])
        }.value
        var iterator = stream.makeAsyncIterator()
        do { _ = try await iterator.next(); XCTFail("authenticated error accepted") }
        catch GoogleOAuth.CallbackError.denied(let message) { XCTAssertEqual(message, "access_denied") }
        await listener.stop()
    }
}
