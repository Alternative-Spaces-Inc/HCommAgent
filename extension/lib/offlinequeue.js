// The background worker owns all queue writes. Every mutation reads the latest
// durable state; acknowledgements remove only the exact attempted entry, so a
// concurrent new answer can never be overwritten by an older flush snapshot.
export const QUEUE_KEY = "hca_answer_queue_v2";
export const MAX_ATTEMPTS = 20;
export const MAX_ENTRIES = 100;
export const MAX_BODY_BYTES = 32768;
export const MAX_QUEUE_BYTES = 1024 * 1024;
export const TTL_MS = 24 * 60 * 60 * 1000;

export function isOfflineError(err) {
  if (!err) return false;
  return err.name === "TypeError" || err.name === "AbortError" || /Failed to fetch|NetworkError|network error|ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED/i.test(String(err.message || err));
}
const byteSize = (value) => new TextEncoder().encode(JSON.stringify(value)).length;
export function enqueue(queue, { requestId, body, origin, deviceId, deadlineAt, now = Date.now(), entryId = globalThis.crypto.randomUUID() }) {
  if (typeof requestId !== "string" || !requestId) throw new Error("requestId is required");
  if (!body || typeof body !== "object") throw new Error("body is required");
  if (!origin || !deviceId) throw new Error("Queue origin and device binding are required");
  if (byteSize(body) > MAX_BODY_BYTES) throw new Error("Answer is too large to save offline");
  const current = prune(queue, now).queue;
  const existing = current.find((e) => e.origin === origin && e.deviceId === deviceId && e.requestId === requestId);
  if (existing) {
    if (JSON.stringify(existing.body) === JSON.stringify(body)) return current;
    throw new Error("An answer for this request is already queued. Wait for delivery before changing it.");
  }
  const deadline = deadlineAt ? Date.parse(deadlineAt) : now + TTL_MS;
  if (!Number.isFinite(deadline) || deadline <= now) throw new Error("This decision has expired; refresh the inbox");
  const entry = { entryId, idempotencyKey: `extension-${entryId}`, requestId, body, origin, deviceId, queuedAt: new Date(now).toISOString(), expiresAt: Math.min(now + TTL_MS, deadline), attempts: 0 };
  const next = [...current, entry];
  if (next.length > MAX_ENTRIES || byteSize(next) > MAX_QUEUE_BYTES) throw new Error("Offline answer storage is full. Connect and deliver pending answers first.");
  return next;
}
export function prune(queue, now = Date.now()) {
  const list = Array.isArray(queue) ? queue : [];
  const kept = list.filter((e) => e.entryId && e.origin && e.deviceId && e.idempotencyKey && e.expiresAt > now && e.attempts < MAX_ATTEMPTS);
  return { queue: kept, dropped: list.length - kept.length };
}
export function classifyOutcome({ status, error }, attempts = 0) {
  if (status >= 200 && status < 300) return "sent";
  if (status === 409 || status === 404 || status === 410) return "stale";
  if (attempts + 1 >= MAX_ATTEMPTS) return "dropped";
  if (error && isOfflineError(error) || status == null || status >= 500) return "retry";
  // Authentication failure must not retain sensitive queued decisions.
  if (status === 401 || status === 403 || status === 422) return "dropped";
  return "rejected";
}
export function applyOutcome(queue, entryId, outcome) {
  if (["sent", "stale", "dropped"].includes(outcome)) return queue.filter((e) => e.entryId !== entryId);
  return queue.map((e) => e.entryId === entryId ? { ...e, attempts: e.attempts + 1, lastAttemptAt: new Date().toISOString() } : e);
}
export function statusOf(err) {
  return err?.status || Number(/answer POST failed: (\d{3})/.exec(String(err?.message || ""))?.[1]) || null;
}
export function serialExecutor() {
  let tail = Promise.resolve();
  return (fn) => {
    const result = tail.then(fn, fn);
    tail = result.catch(() => {});
    return result;
  };
}

// This instance exists only in the service worker. The injected adapter is
// used for race and worker-restart tests, without a Chrome dependency.
export function createAnswerQueue(storage, { now = Date.now } = {}) {
  const serialize = serialExecutor();
  const read = async () => (await storage.get(QUEUE_KEY))[QUEUE_KEY] || [];
  const write = (queue) => storage.set({ [QUEUE_KEY]: queue });
  let flushing;
  async function snapshot() {
    return serialize(async () => {
      const result = prune(await read(), now());
      if (result.dropped) await write(result.queue);
      return result;
    });
  }
  return {
    snapshot,
    enqueue: (entry) => serialize(async () => {
      const next = enqueue(await read(), { ...entry, now: now() });
      await write(next);
      return next.find((e) => e.origin === entry.origin && e.deviceId === entry.deviceId && e.requestId === entry.requestId);
    }),
    clear: (predicate) => serialize(async () => { const list = await read(); const kept = list.filter((e) => !predicate(e)); await write(kept); return list.length - kept.length; }),
    flush(postAnswerFn, scope, { isCurrent = () => true } = {}) {
      if (flushing) return flushing;
      flushing = (async () => {
        const initial = await snapshot();
        const counts = { sent: 0, stale: 0, kept: 0, dropped: initial.dropped };
        for (const entry of initial.queue.filter((e) => e.origin === scope.origin && e.deviceId === scope.deviceId)) {
          if (!isCurrent()) break;
          let outcome, error;
          try { await postAnswerFn(entry); outcome = "sent"; }
          catch (e) { error = e; outcome = classifyOutcome({ status: statusOf(e), error: e }, entry.attempts); }
          await serialize(async () => { await write(applyOutcome(await read(), entry.entryId, outcome)); });
          if (outcome === "sent" || outcome === "stale" || outcome === "dropped") counts[outcome] += 1;
          else counts.kept += 1;
          if (error && isOfflineError(error)) break;
        }
        return counts;
      })().finally(() => { flushing = null; });
      return flushing;
    },
  };
}
