import { test } from "node:test";
import assert from "node:assert/strict";
import { enqueue, createAnswerQueue, classifyOutcome, applyOutcome, prune, isOfflineError, statusOf, MAX_ATTEMPTS, MAX_ENTRIES, MAX_BODY_BYTES, TTL_MS, QUEUE_KEY } from "../lib/offlinequeue.js";
const body = { control: "radio", value: "b", surface: "browser_overlay" };
const scope = { origin: "https://api.example", deviceId: "dev-one" };
const entry = (requestId, extra = {}) => ({ ...scope, requestId, body, ...extra });
function storage(initial = []) {
  const data = { [QUEUE_KEY]: initial };
  return { data, async get(key) { return { [key]: structuredClone(data[key]) }; }, async set(value) { Object.assign(data, structuredClone(value)); } };
}
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }

test("queue binds identity and origin, retains stable retry identity, and refuses silent offline edits", () => {
  let queue = enqueue([], entry("r1"));
  const stable = queue[0].idempotencyKey;
  queue = enqueue(queue, entry("r1"));
  assert.equal(queue.length, 1); assert.equal(queue[0].idempotencyKey, stable);
  assert.throws(() => enqueue(queue, entry("r1", { body: { ...body, value: "a" } })), /already queued/);
  assert.throws(() => enqueue([], { requestId: "r1", body }), /binding/);
  assert.equal(enqueue(queue, entry("r1", { origin: "https://other.example" })).length, 2);
});
test("queue enforces expiry, attempts, entry count and body size", () => {
  const now = 1000;
  const queue = enqueue([], entry("r1", { now }));
  assert.equal(queue[0].expiresAt, now + TTL_MS);
  assert.equal(prune(queue, now + TTL_MS).dropped, 1);
  assert.throws(() => enqueue([], entry("r1", { now, deadlineAt: new Date(now - 1).toISOString() })), /expired/);
  assert.throws(() => enqueue([], entry("r1", { body: { value: "x".repeat(MAX_BODY_BYTES) } })), /too large/);
  let full = [];
  for (let i = 0; i < MAX_ENTRIES; i++) full = enqueue(full, entry(String(i)));
  assert.throws(() => enqueue(full, entry("overflow")), /full/);
  assert.equal(classifyOutcome({ error: new TypeError("Failed to fetch") }, MAX_ATTEMPTS - 1), "dropped");
  assert.equal(classifyOutcome({ status: 503 }, MAX_ATTEMPTS - 1), "dropped");
  assert.equal(classifyOutcome({ status: 403 }), "dropped");
});
test("transport and server outcomes preserve ambiguous successes for idempotent retry", () => {
  assert.equal(isOfflineError(new TypeError("Failed to fetch")), true);
  assert.equal(isOfflineError(new Error("answer POST failed: 500")), false);
  assert.equal(statusOf(new Error("answer POST failed: 409 already answered")), 409);
  assert.equal(classifyOutcome({ status: 200 }), "sent");
  for (const status of [404, 409, 410]) assert.equal(classifyOutcome({ status }), "stale");
  assert.equal(classifyOutcome({ status: 503 }), "retry");
  assert.equal(classifyOutcome({ status: 429 }), "retry");
});
test("permanent rejection removes the saved answer immediately instead of retrying silently", async () => {
  const memory = storage(); const queue = createAnswerQueue(memory);
  await queue.enqueue(entry("r1"));
  const result = await queue.flush(async () => { const error = new Error("Invalid answer"); error.status = 422; throw error; }, scope);
  assert.equal(result.rejected, 1);
  assert.equal(result.kept, 0);
  assert.deepEqual(memory.data[QUEUE_KEY], []);
});
test("enqueue during a slow flush survives acknowledgement of the older snapshot", async () => {
  const memory = storage(); const queue = createAnswerQueue(memory);
  const first = await queue.enqueue(entry("r1"));
  const started = deferred(), finish = deferred();
  const flushing = queue.flush(async (saved) => { assert.equal(saved.idempotencyKey, first.idempotencyKey); started.resolve(); await finish.promise; }, scope);
  await started.promise;
  const second = await queue.enqueue(entry("r2"));
  finish.resolve(); await flushing;
  assert.deepEqual(memory.data[QUEUE_KEY].map((item) => item.entryId), [second.entryId]);
});
test("two popups enqueue concurrently without lost answers or duplicate same-request entries", async () => {
  const memory = storage(); const queue = createAnswerQueue(memory);
  await Promise.all(Array.from({ length: 20 }, (_, i) => queue.enqueue(entry(`r${i}`))));
  assert.equal(memory.data[QUEUE_KEY].length, 20);
  const same = await Promise.all(Array.from({ length: 10 }, () => queue.enqueue(entry("same"))));
  assert.equal(new Set(same.map((saved) => saved.entryId)).size, 1);
  assert.equal(memory.data[QUEUE_KEY].length, 21);
});
test("restart after an ambiguous success resends the same key, never to a changed server/device", async () => {
  const memory = storage(); let queue = createAnswerQueue(memory);
  const saved = await queue.enqueue(entry("r1"));
  const keys = [];
  await queue.flush(async (item) => { keys.push(item.idempotencyKey); throw new TypeError("Failed to fetch"); }, scope);
  queue = createAnswerQueue(memory);
  await queue.flush(async () => assert.fail("cross-origin send"), { ...scope, origin: "https://other.example" });
  await queue.flush(async () => assert.fail("cross-device send"), { ...scope, deviceId: "dev-two" });
  await queue.flush(async (item) => keys.push(item.idempotencyKey), scope);
  assert.deepEqual(keys, [saved.idempotencyKey, saved.idempotencyKey]);
  assert.equal(memory.data[QUEUE_KEY].length, 0);
});
test("concurrent flush triggers share a single in-flight send", async () => {
  const queue = createAnswerQueue(storage()); await queue.enqueue(entry("r1"));
  const finish = deferred(); let calls = 0;
  const post = async () => { calls += 1; await finish.promise; };
  const a = queue.flush(post, scope), b = queue.flush(post, scope);
  assert.equal(a, b); finish.resolve(); await a; assert.equal(calls, 1);
});
test("acknowledgements only remove the attempted immutable entry", () => {
  const list = enqueue(enqueue([], entry("r1")), entry("r2"));
  assert.deepEqual(applyOutcome(list, list[0].entryId, "sent").map((item) => item.requestId), ["r2"]);
  assert.equal(applyOutcome(list, list[0].entryId, "retry")[0].attempts, 1);
});
