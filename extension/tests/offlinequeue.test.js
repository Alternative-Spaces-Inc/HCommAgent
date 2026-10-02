import { test } from "node:test";
import assert from "node:assert/strict";
import { enqueue, classifyOutcome, applyOutcome, isOfflineError, statusOf, flushQueue, MAX_ATTEMPTS, QUEUE_KEY } from "../lib/offlinequeue.js";

const body = { control: "radio", value: "b", surface: "browser_overlay" };

test("enqueue adds one entry per request and replaces an earlier answer for the same ask", () => {
  let q = enqueue([], { requestId: "r1", body, queuedAt: "t1" });
  q = enqueue(q, { requestId: "r2", body, queuedAt: "t2" });
  q = enqueue(q, { requestId: "r1", body: { ...body, value: "a" }, queuedAt: "t3" });
  assert.equal(q.length, 2);
  assert.deepEqual(q.map((e) => e.requestId), ["r2", "r1"]);
  assert.equal(q[1].body.value, "a");
  assert.equal(q[1].attempts, 0);
});

test("enqueue refuses an entry without request id or body", () => {
  assert.throws(() => enqueue([], { body }), /requestId/);
  assert.throws(() => enqueue([], { requestId: "r1" }), /body/);
});

test("isOfflineError recognises fetch transport failures only", () => {
  assert.equal(isOfflineError(new TypeError("Failed to fetch")), true);
  assert.equal(isOfflineError(new Error("NetworkError when attempting to fetch resource.")), true);
  assert.equal(isOfflineError(new Error("answer POST failed: 500 boom")), false);
  assert.equal(isOfflineError(null), false);
});

test("classifyOutcome: 2xx sent, 409/404/410 stale, 5xx and transport retry", () => {
  assert.equal(classifyOutcome({ status: 200 }), "sent");
  assert.equal(classifyOutcome({ status: 409 }), "stale");
  assert.equal(classifyOutcome({ status: 404 }), "stale");
  assert.equal(classifyOutcome({ status: 410 }), "stale");
  assert.equal(classifyOutcome({ status: 503 }), "retry");
  assert.equal(classifyOutcome({ status: null, error: new TypeError("Failed to fetch") }), "retry");
});

test("classifyOutcome: other 4xx is kept until MAX_ATTEMPTS, then dropped", () => {
  assert.equal(classifyOutcome({ status: 403 }, 0), "rejected");
  assert.equal(classifyOutcome({ status: 403 }, MAX_ATTEMPTS - 2), "rejected");
  assert.equal(classifyOutcome({ status: 403 }, MAX_ATTEMPTS - 1), "dropped");
});

test("applyOutcome removes sent/stale/dropped and counts attempts otherwise", () => {
  const q = enqueue(enqueue([], { requestId: "r1", body }), { requestId: "r2", body });
  assert.deepEqual(applyOutcome(q, "r1", "sent").map((e) => e.requestId), ["r2"]);
  assert.deepEqual(applyOutcome(q, "r1", "stale").map((e) => e.requestId), ["r2"]);
  const kept = applyOutcome(q, "r2", "rejected");
  assert.equal(kept.length, 2);
  assert.equal(kept[1].attempts, 1);
  assert.ok(kept[1].lastAttemptAt);
});

test("statusOf parses the postAnswer error text", () => {
  assert.equal(statusOf(new Error("answer POST failed: 409 {\"detail\":\"request already answered\"}")), 409);
  assert.equal(statusOf(new TypeError("Failed to fetch")), null);
});

function fakeStorage(initial) {
  const data = { [QUEUE_KEY]: initial };
  return {
    data,
    async get(k) { return { [k]: data[k] }; },
    async set(obj) { Object.assign(data, obj); },
  };
}

test("flushQueue sends every entry when online and empties the queue", async () => {
  const storage = fakeStorage(enqueue(enqueue([], { requestId: "r1", body }), { requestId: "r2", body }));
  const posted = [];
  const counts = await flushQueue(async (id, b) => { posted.push([id, b.value]); return {}; }, storage);
  assert.deepEqual(posted, [["r1", "b"], ["r2", "b"]]);
  assert.deepEqual(counts, { sent: 2, stale: 0, kept: 0, dropped: 0 });
  assert.deepEqual(storage.data[QUEUE_KEY], []);
});

test("flushQueue drops a 409 (answered elsewhere) and keeps going", async () => {
  const storage = fakeStorage(enqueue(enqueue([], { requestId: "r1", body }), { requestId: "r2", body }));
  const counts = await flushQueue(async (id) => {
    if (id === "r1") throw new Error("answer POST failed: 409 {\"detail\":\"request already answered\"}");
    return {};
  }, storage);
  assert.deepEqual(counts, { sent: 1, stale: 1, kept: 0, dropped: 0 });
  assert.deepEqual(storage.data[QUEUE_KEY], []);
});

test("flushQueue stops at the first transport failure and keeps everything", async () => {
  const storage = fakeStorage(enqueue(enqueue([], { requestId: "r1", body }), { requestId: "r2", body }));
  let calls = 0;
  const counts = await flushQueue(async () => { calls += 1; throw new TypeError("Failed to fetch"); }, storage);
  assert.equal(calls, 1);
  assert.equal(counts.kept, 2);
  assert.equal(storage.data[QUEUE_KEY].length, 2);
  assert.equal(storage.data[QUEUE_KEY][0].attempts, 1);
});
