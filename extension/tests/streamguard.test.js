import { test } from "node:test";
import assert from "node:assert/strict";
import { createBackground } from "../background.js";
import { stateKey, API_BASE_KEY } from "../lib/state.js";
import { QUEUE_KEY } from "../lib/offlinequeue.js";
const origin = "https://api.example";
const deviceId = "dev-test";
const request = (id = "r1") => ({ request_id: id, title: "Secret decision", why: "Secret context", control: "radio", options_or_fields: [{ id: "one", label: "One" }], suggested: {}, expiry_policy: "hold" });
const event = () => { const listeners = []; return { addListener(fn) { listeners.push(fn); }, emit(...args) { for (const fn of listeners) fn(...args); } }; };
function mockChrome(initial = {}) {
  const data = { [API_BASE_KEY]: origin, ...initial };
  const onChanged = event(); const notifications = {};
  return {
    data, shown: [], alarmsMade: [], badges: [],
    storage: { onChanged, local: {
      async get(keys) { if (keys === null) return structuredClone(data); if (!Array.isArray(keys)) keys = [keys]; return Object.fromEntries(keys.map((key) => [key, structuredClone(data[key])])); },
      async set(value) { const changes = Object.fromEntries(Object.entries(value).map(([key, newValue]) => [key, { oldValue: data[key], newValue }])); Object.assign(data, structuredClone(value)); onChanged.emit(changes, "local"); },
      async remove(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) delete data[key]; },
      async setAccessLevel() {},
    } },
    runtime: { id: "test-extension", getURL: (path) => `chrome-extension://test-extension/${path}`, onMessage: event(), onInstalled: event(), onStartup: event() },
    notifications: { onClicked: event(), async create(id, value) { notifications[id] = value; this.shown?.push(value); }, async clear(id) { delete notifications[id]; }, async getAll() { return notifications; } },
    action: { async setBadgeText() {}, async openPopup() {} },
    alarms: { onAlarm: event(), async create() {} },
    permissions: { async contains() { return true; } },
  };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
function deps(extra = {}) {
  return { currentDevice: async () => ({ deviceId }), fetchInbox: async () => [], postAnswer: async () => ({}), signedFetch: async () => ({ ok: true, body: new ReadableStream({ start(c) { c.close(); } }) }), ...extra };
}
test("concurrent startup and alarm connection share one stream even during fetch", async () => {
  const chrome = mockChrome(); let calls = 0, release;
  const waiting = new Promise((resolve) => { release = resolve; });
  const worker = createBackground(chrome, deps({ signedFetch: async () => { calls++; await waiting; return { ok: true, body: new ReadableStream({ start(c) { c.close(); } }) }; } }));
  const a = worker.connectStream(), b = worker.connectStream(); assert.equal(a, b);
  await tick(); assert.equal(calls, 1); release(); await a;
  await worker.connectStream(); assert.equal(calls, 2);
});
test("origin switch aborts a live old stream and stale events cannot populate the new cache", async () => {
  const chrome = mockChrome(); let signal, stream;
  const worker = createBackground(chrome, deps({ signedFetch: async (_, init) => {
    signal = init.signal;
    return { ok: true, body: new ReadableStream({ start(controller) { stream = controller; signal.addEventListener("abort", () => controller.close()); } }) };
  } }));
  const originalScope = await worker.scope(); const running = worker.connectStream(); await tick();
  await chrome.storage.local.set({ [API_BASE_KEY]: "https://other.example" });
  assert.equal(signal.aborted, true);
  await worker.handleStreamEvent({ event: "request.created", id: "9", data: request() }, originalScope);
  await tick();
  assert.equal(chrome.data[stateKey({ origin: "https://other.example", deviceId })]?.cache.r1, undefined);
  await running;
  stream.close();
});
test("a popup open at the old server cannot submit its cached decision after an origin change", async () => {
  const chrome = mockChrome({ [stateKey({ origin, deviceId })]: { cache: { r1: request() }, cursor: "5" } });
  let posts = 0;
  const worker = createBackground(chrome, deps({ postAnswer: async () => { posts++; } }));
  await chrome.storage.local.set({ [API_BASE_KEY]: "https://other.example" });
  await assert.rejects(worker.message({ type: "hca_submit_answer", origin, deviceId, requestId: "r1", control: "radio", selection: "one" }), /configuration changed/);
  assert.equal(posts, 0);
});
test("worker restart uses the scoped cursor and reconciles lost inbox events", async () => {
  const chrome = mockChrome({ [stateKey({ origin, deviceId })]: { cache: {}, cursor: "42" } });
  let headers;
  const worker = createBackground(chrome, deps({ fetchInbox: async () => [request()], signedFetch: async (_, init) => { headers = init.headers; return { ok: true, body: new ReadableStream({ start(c) { c.close(); } }) }; } }));
  await worker.pollInbox(); await worker.connectStream();
  assert.equal(headers["Last-Event-ID"], "42");
  assert.equal(chrome.data[stateKey({ origin, deviceId })].cache.r1.title, "Secret decision");
  const restarted = createBackground(chrome, deps()); const snapshot = await restarted.message({ type: "hca_snapshot" });
  assert.equal(snapshot.cache.r1.request_id, "r1");
});
test("revocation removes scoped inbox and queued data", async () => {
  const chrome = mockChrome({ [stateKey({ origin, deviceId })]: { cache: { r1: request() }, cursor: "10" } });
  const worker = createBackground(chrome, deps({ fetchInbox: async () => { const error = new Error("refused"); error.status = 403; throw error; } }));
  await worker.pollInbox();
  assert.equal(chrome.data[stateKey({ origin, deviceId })], undefined);
  assert.deepEqual(chrome.data[QUEUE_KEY], []);
  assert.match(chrome.data.hca_delivery_notice, /access was refused/);
});
test("replay gap clears old cursor and immediately resynchronizes", async () => {
  const chrome = mockChrome({ [stateKey({ origin, deviceId })]: { cache: {}, cursor: "10" } });
  const worker = createBackground(chrome, deps({ fetchInbox: async () => [request("recovered")] }));
  await worker.handleStreamEvent({ event: "stream.reset", data: { reason: "gap" } }, await worker.scope());
  await worker.pollInbox();
  assert.equal(chrome.data[stateKey({ origin, deviceId })].cursor, null);
  assert.equal(chrome.data[stateKey({ origin, deviceId })].cache.recovered.request_id, "recovered");
});

test("notification previews are redacted by default and deliberate opt-in reveals context", async () => {
  const chrome = mockChrome();
  const worker = createBackground(chrome, deps({ fetchInbox: async () => [request()] }));
  await worker.pollInbox();
  let notifications = await chrome.notifications.getAll();
  assert.equal(notifications["hca-r1"].title, "A decision is waiting");
  assert.doesNotMatch(notifications["hca-r1"].message, /Secret/);
  await chrome.storage.local.set({ hca_notification_previews: true });
  await worker.handleStreamEvent({ event: "request.created", data: request("r2") }, await worker.scope());
  notifications = await chrome.notifications.getAll();
  assert.equal(notifications["hca-r2"].title, "Secret decision");
});

test("configuration changes discard old scoped cache, cursor, queue and notifications", async () => {
  const chrome = mockChrome({ [stateKey({ origin, deviceId })]: { cache: { r1: request() }, cursor: "42", focusId: "r1" } });
  const worker = createBackground(chrome, deps({ postAnswer: async () => { throw new TypeError("Failed to fetch"); } }));
  await worker.message({ type: "hca_submit_answer", origin, deviceId, requestId: "r1", control: "radio", selection: "one" });
  await tick(); assert.equal(chrome.data[QUEUE_KEY].length, 1);
  await chrome.notifications.create("hca-r1", { title: "Old" });
  await chrome.storage.local.set({ [API_BASE_KEY]: "https://new.example" });
  await tick(); await tick();
  assert.equal(chrome.data[stateKey({ origin, deviceId })], undefined);
  assert.equal(chrome.data[QUEUE_KEY].length, 0);
  assert.deepEqual(await chrome.notifications.getAll(), {});
  assert.match(chrome.data.hca_delivery_notice, /discarded/);
});

test("revoked SSE frame clears private state and later polls can recover after relinking", async () => {
  const chrome = mockChrome({ [stateKey({ origin, deviceId })]: { cache: { r1: request() }, cursor: "10" } });
  let requests = 0;
  const worker = createBackground(chrome, deps({ fetchInbox: async () => { requests++; return [request("relinked")]; }, signedFetch: async () => ({ ok: true, body: new ReadableStream({ start(controller) {
    controller.enqueue(new TextEncoder().encode('event: stream.closed\ndata: {"reason":"authorization revoked"}\n\n'));
    controller.close();
  } }) }) }));
  await worker.connectStream();
  assert.equal(chrome.data[stateKey({ origin, deviceId })], undefined);
  assert.match(chrome.data.hca_delivery_notice, /revoked/);
  await worker.pollInbox();
  assert.equal(requests, 1);
  assert.ok(chrome.data[stateKey({ origin, deviceId })].cache.relinked);
});

test("configuration changes during key initialization cannot open an extra stream with the new scope", async () => {
  const chrome = mockChrome(); let resolveDevice;
  const blockedDevice = new Promise((resolve) => { resolveDevice = resolve; });
  let fetches = 0;
  const worker = createBackground(chrome, deps({ currentDevice: async () => blockedDevice, signedFetch: async () => {
    fetches++;
    return { ok: true, body: new ReadableStream({ start(controller) { controller.close(); } }) };
  } }));
  const old = worker.connectStream(); await tick();
  await chrome.storage.local.set({ [API_BASE_KEY]: "https://new.example" });
  resolveDevice({ deviceId });
  await old; await tick(); await tick();
  assert.equal(fetches, 1);
});

test("upgrade discards unbound legacy decisions with a visible explanation", async () => {
  const chrome = mockChrome({ hca_inbox_cache: { secret: request() }, hca_answer_queue: [{ requestId: "secret", body: { value: "one" } }], hca_last_event_id: "99" });
  const worker = createBackground(chrome, deps());
  const snapshot = await worker.message({ type: "hca_snapshot" });
  for (const key of ["hca_inbox_cache", "hca_answer_queue", "hca_last_event_id"]) assert.equal(chrome.data[key], undefined);
  assert.match(snapshot.notice, /security upgrade/);
  assert.deepEqual(snapshot.queue, []);
});
