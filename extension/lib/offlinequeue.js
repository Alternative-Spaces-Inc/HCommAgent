// Offline answer queue (HCA-P12, plan Day 8).
//
// When the popup cannot reach the API (no response at all: fetch threw),
// the answer is stored in chrome.storage.local and the service worker
// replays it on its next poll or stream reconnect. The queue holds the
// unsigned body: device signatures carry a timestamp and a single-use
// nonce the server checks, so a signature made offline would be stale by
// the time the network is back. Signing happens at send time, on-device,
// through the same postAnswer path the popup uses.
//
// Pure helpers live here so they can be tested without chrome.*.

export const QUEUE_KEY = "hca_answer_queue";
export const MAX_ATTEMPTS = 20;

// Only a transport failure is an offline condition. A server reply, even a
// 5xx, means we were online; retrying those risks double-posting a human
// decision, and the popup shows the error instead.
export function isOfflineError(err) {
  if (!err) return false;
  if (err.name === "TypeError") return true; // fetch(): network failure or blocked
  const msg = String(err.message || err);
  return /Failed to fetch|NetworkError|network error|ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED/i.test(msg);
}

// One entry per request id; a later answer for the same ask replaces the
// earlier one (the owner changed their mind while still offline).
export function enqueue(queue, { requestId, body, queuedAt }) {
  if (!requestId) throw new Error("requestId is required");
  if (!body || typeof body !== "object") throw new Error("body is required");
  const rest = (queue || []).filter((e) => e.requestId !== requestId);
  return [...rest, { requestId, body, queuedAt: queuedAt || new Date().toISOString(), attempts: 0 }];
}

// Decides what a flush attempt outcome means for the entry.
//   sent:     2xx, drop
//   stale:    409 (already answered, cancelled or expired), 404 or 410, drop;
//             the decision is moot and the audit row already exists
//   retry:    transport failure or 5xx, keep, attempts + 1
//   rejected: any other 4xx (bad signature, unlinked device), keep up to
//             MAX_ATTEMPTS so a device the owner is about to re-link still
//             delivers, then drop with a notice
export function classifyOutcome({ status, error }, attempts = 0) {
  if (error && isOfflineError(error)) return "retry";
  if (status == null) return "retry";
  if (status >= 200 && status < 300) return "sent";
  if (status === 409 || status === 404 || status === 410) return "stale";
  if (status >= 500) return "retry";
  return attempts + 1 >= MAX_ATTEMPTS ? "dropped" : "rejected";
}

// Applies one outcome to the queue; returns the new queue.
export function applyOutcome(queue, requestId, outcome) {
  if (outcome === "sent" || outcome === "stale" || outcome === "dropped") {
    return queue.filter((e) => e.requestId !== requestId);
  }
  return queue.map((e) => (e.requestId === requestId ? { ...e, attempts: (e.attempts || 0) + 1, lastAttemptAt: new Date().toISOString() } : e));
}

export function statusOf(err) {
  // postAnswer throws "answer POST failed: <status> <text>"
  const m = /answer POST failed: (\d{3})/.exec(String(err && err.message || ""));
  return m ? Number(m[1]) : null;
}

// chrome.storage-backed wrappers (not unit tested; thin by design).
export async function readQueue(storage = chrome.storage.local) {
  const stored = await storage.get(QUEUE_KEY);
  return stored[QUEUE_KEY] || [];
}

export async function writeQueue(queue, storage = chrome.storage.local) {
  await storage.set({ [QUEUE_KEY]: queue });
}

// Replays every queued answer through postAnswerFn(requestId, body).
// Returns { sent, stale, kept, dropped } counts. Stops early on the first
// transport failure (still offline), leaving the rest for the next flush.
export async function flushQueue(postAnswerFn, storage = chrome.storage.local) {
  let queue = await readQueue(storage);
  const counts = { sent: 0, stale: 0, kept: 0, dropped: 0 };
  for (const entry of [...queue]) {
    let outcome;
    try {
      await postAnswerFn(entry.requestId, entry.body);
      outcome = "sent";
    } catch (e) {
      outcome = classifyOutcome({ status: statusOf(e), error: e }, entry.attempts || 0);
      if (outcome === "retry" && isOfflineError(e)) {
        queue = applyOutcome(queue, entry.requestId, outcome);
        counts.kept += queue.filter((q) => q.requestId !== entry.requestId).length + 1;
        await writeQueue(queue, storage);
        return counts;
      }
    }
    queue = applyOutcome(queue, entry.requestId, outcome);
    if (outcome === "sent") counts.sent += 1;
    else if (outcome === "stale") counts.stale += 1;
    else if (outcome === "dropped") counts.dropped += 1;
    else counts.kept += 1;
  }
  await writeQueue(queue, storage);
  return counts;
}
