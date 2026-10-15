// Scenarios 2 (reconnect), 5 (concurrent queue writes), 6 (notifications), 8 (malformed events).
import assert from "node:assert/strict";
import { ORIGIN, bootWorker, sampleItem, sleep } from "./harness.js";

const sse = (event, data) => `event: ${event}\ndata: ${JSON.stringify({ event, data, timestamp: 1 })}\n\n`;

export async function reconnect(context, base) {
  let inbox = [sampleItem("a"), sampleItem("b")]; let streams = 0;
  await context.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/v1/inbox") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(inbox) });
    if (url.pathname === "/v1/inbox/stream") {
      streams += 1;
      // The first stream delivers one event and then the connection closes mid stream.
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: streams === 1 ? sse("request.created", sampleItem("b")) : ": connected\n\n" });
    }
    return route.fulfill({ status: 404, body: "" });
  });
  const worker = await bootWorker(context, base);
  await worker.wake();
  await worker.page.waitForFunction(() => Object.values(__hca.store).some((v) => v && v.cache && v.cache.a && v.cache.b));
  assert.equal(streams, 1);
  // Server state moves on while the stream is down; the alarm poll must pick it up.
  inbox = [sampleItem("a"), sampleItem("b"), sampleItem("c")];
  await worker.page.evaluate(() => __hca.listeners.alarm.forEach((fn) => fn({ name: "hca-inbox-poll" })));
  await worker.page.waitForFunction(() => Object.values(__hca.store).some((v) => v && v.cache && v.cache.c));
  await sleep(200);
  assert.equal(streams, 2, "the wake after closure opened exactly one new stream connection");
  await worker.page.close(); await context.unroute(`${ORIGIN}/**`);
  console.log("PASS: reconnect, alarm poll kept the inbox current after the stream closed, one new stream connection");
}

export async function concurrentQueueWrites(context, base) {
  const posts = [];
  await context.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/v1/inbox") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([sampleItem("x"), sampleItem("y")]) });
    if (url.pathname === "/v1/inbox/stream") return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": connected\n\n" });
    if (url.pathname.endsWith("/answer")) { posts.push(url.pathname); return route.abort("internetdisconnected"); }
    return route.fulfill({ status: 404, body: "" });
  });
  const worker = await bootWorker(context, base);
  await worker.wake();
  await worker.page.waitForFunction(() => Object.values(__hca.store).some((v) => v && v.cache && v.cache.x && v.cache.y));
  const deviceId = await worker.deviceId();
  // Two popups answer two asks within the same tick.
  const results = await worker.page.evaluate(({ origin, deviceId }) => Promise.all(["x", "y"].map((id) =>
    chrome.runtime.sendMessage({ type: "hca_submit_answer", origin, deviceId, requestId: id, control: "approve_deny", selection: "approve" }))), { origin: ORIGIN, deviceId });
  assert.ok(results.every((r) => r.queued));
  await sleep(400);
  const queue = await worker.queue();
  assert.equal(queue.length, 2, "two entries, no lost write");
  assert.deepEqual(queue.map((e) => e.requestId).sort(), ["x", "y"]);
  assert.equal(new Set(queue.map((e) => e.idempotencyKey)).size, 2);
  await worker.page.close(); await context.unroute(`${ORIGIN}/**`);
  console.log("PASS: concurrent queue writes from two popups in one tick, two entries, distinct keys");
}

export async function notifications(context, base) {
  await context.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/v1/inbox") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([sampleItem("n1")]) });
    if (url.pathname === "/v1/inbox/stream") return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": connected\n\n" });
    return route.fulfill({ status: 404, body: "" });
  });
  const worker = await bootWorker(context, base);
  await worker.wake();
  await worker.page.waitForFunction(() => __hca.calls.some((c) => c.name === "notifications.create" && c.args[0] === "hca-n1"));
  const created = (await worker.calls("notifications.create"))[0];
  assert.equal(created.args[1].title, "A decision is waiting", "no preview text without opt in");
  // Click with the popup available.
  let result = await worker.page.evaluate(() => bg.notificationClicked("hca-n1"));
  assert.equal(result.surface, "popup");
  let store = await worker.store();
  assert.ok(Object.values(store).some((v) => v && v.focusId === "n1"), "focusId set for the clicked ask");
  // Popup refused: a decision tab opens, a second click focuses it, a foreign id is ignored.
  await worker.page.evaluate(() => { __hca.openPopupRejects = true; });
  result = await worker.page.evaluate(() => bg.notificationClicked("hca-n1"));
  assert.equal(result.surface, "tab"); assert.equal(result.reused, false);
  const tabs = await worker.calls("tabs.create");
  assert.equal(tabs.length, 1); assert.match(tabs[0].args[0], /decision\.html\?request=n1$/);
  result = await worker.page.evaluate(() => bg.notificationClicked("hca-n1"));
  assert.equal(result.reused, true); assert.equal((await worker.calls("tabs.create")).length, 1);
  assert.equal(await worker.page.evaluate(() => bg.notificationClicked("other-n1")), null);
  await worker.page.close(); await context.unroute(`${ORIGIN}/**`);
  console.log("PASS: notifications, click focuses the ask, popup first, refused popup opens one decision tab and reuses it");
}

export async function malformedEvents(context, base) {
  const bad = [
    "event: request.created\ndata: {not json\n\n",
    `event: request.created\ndata: ${JSON.stringify({ event: "request.created", data: { request_id: "far", visible_to: ["someone-else"] } })}\n\n`,
    "event: totally.unknown\ndata: {\"event\":\"totally.unknown\",\"data\":{\"request_id\":\"u\"}}\n\n",
    `event: request.created\ndata: ${JSON.stringify({ event: "request.created", data: { request_id: "big", title: "x".repeat(300000) } })}\n\n`,
  ].join("");
  await context.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    // The poll reconciles to the server inbox, so the inbox carries the one good ask too.
    if (url.pathname === "/v1/inbox") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([sampleItem("good")]) });
    if (url.pathname === "/v1/inbox/stream") return route.fulfill({ status: 200, contentType: "text/event-stream", body: bad + sse("request.created", sampleItem("good")) });
    return route.fulfill({ status: 404, body: "" });
  });
  const worker = await bootWorker(context, base);
  const errors = []; worker.page.on("pageerror", (e) => errors.push(String(e)));
  await worker.wake();
  await worker.page.waitForFunction(() => Object.values(__hca.store).some((v) => v && v.cache && v.cache.good), null, { timeout: 10000 });
  await sleep(500);
  const store = await worker.store();
  const cache = Object.values(store).find((v) => v && v.cache).cache;
  assert.deepEqual(Object.keys(cache), ["good"], "only the well formed addressed event is rendered");
  assert.equal(errors.length, 0, "no uncaught error in the worker");
  await worker.page.close(); await context.unroute(`${ORIGIN}/**`);
  console.log("PASS: malformed events (bad JSON, foreign device, unknown type, oversized) dropped, the next good event still lands");
}
