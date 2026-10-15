// Scenarios 1 (offline answer) and 3 (worker restart) of plan section 4.
import assert from "node:assert/strict";
import { ORIGIN, bootWorker, sampleItem, sleep } from "./harness.js";

export async function offlineAnswer(context, base) {
  const posts = []; let online = false;
  await context.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/v1/inbox") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([sampleItem("r1")]) });
    if (url.pathname === "/v1/inbox/stream") return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": connected\n\n" });
    if (url.pathname.endsWith("/answer")) {
      posts.push({ key: route.request().headers()["idempotency-key"], online });
      if (!online) return route.abort("internetdisconnected");
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
    }
    return route.fulfill({ status: 404, body: "" });
  });
  const worker = await bootWorker(context, base);
  await worker.wake();
  await worker.page.waitForFunction(() => Object.values(__hca.store).some((v) => v && v.cache && v.cache.r1));
  const deviceId = await worker.deviceId();
  const answer = await worker.message({ type: "hca_submit_answer", origin: ORIGIN, deviceId, requestId: "r1", control: "approve_deny", selection: "approve" });
  assert.equal(answer.queued, true);
  await worker.page.waitForFunction(() => __hca.calls.length > 0);
  await sleep(300);
  let queue = await worker.queue();
  assert.equal(queue.length, 1, "one queue entry while offline");
  assert.ok(posts.length >= 1 && posts.every((p) => !p.online), "offline attempts aborted");
  const key = queue[0].idempotencyKey; assert.ok(key, "entry carries an Idempotency-Key");
  online = true;
  await worker.wake();
  await worker.page.waitForFunction(() => (__hca.store.hca_answer_queue_v2 || []).length === 0, null, { timeout: 10000 });
  const delivered = posts.filter((p) => p.online);
  assert.equal(delivered.length, 1, "exactly one POST once online");
  assert.ok(posts.every((p) => p.key === key), "every attempt carried the same Idempotency-Key");
  await worker.page.close(); await context.unroute(`${ORIGIN}/**`);
  console.log("PASS: offline answer, one queue entry, one delivery with an unchanged Idempotency-Key, empty queue");
}

export async function workerRestart(context, base) {
  const posts = []; let online = false;
  await context.route(`${ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/v1/inbox") return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([sampleItem("r3")]) });
    if (url.pathname === "/v1/inbox/stream") return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": connected\n\n" });
    if (url.pathname.endsWith("/answer")) { posts.push({ key: route.request().headers()["idempotency-key"], online }); return online ? route.fulfill({ status: 200, contentType: "application/json", body: "{}" }) : route.abort("internetdisconnected"); }
    return route.fulfill({ status: 404, body: "" });
  });
  const worker = await bootWorker(context, base);
  await worker.wake();
  await worker.page.waitForFunction(() => Object.values(__hca.store).some((v) => v && v.cache && v.cache.r3));
  const deviceId = await worker.deviceId();
  await worker.message({ type: "hca_submit_answer", origin: ORIGIN, deviceId, requestId: "r3", control: "approve_deny", selection: "approve" });
  await sleep(300);
  const before = await worker.queue(); assert.equal(before.length, 1);
  // Chrome terminates the worker: a fresh instance over the same storage is what comes back.
  online = true;
  await worker.restart();
  await worker.wake();
  await worker.page.waitForFunction(() => (__hca.store.hca_answer_queue_v2 || []).length === 0, null, { timeout: 10000 });
  const delivered = posts.filter((p) => p.online);
  assert.equal(delivered.length, 1, "the revived worker flushed the queue exactly once");
  assert.equal(delivered[0].key, before[0].idempotencyKey, "Idempotency-Key unchanged across the restart");
  await worker.page.close(); await context.unroute(`${ORIGIN}/**`);
  console.log("PASS: worker restart, queue flushed once by the revived worker, Idempotency-Key unchanged");
}
