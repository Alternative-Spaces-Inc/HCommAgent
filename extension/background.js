// MV3 streams are a fast path. The persistent alarm reconciles the inbox
// after suspension, replay gaps, disconnects, and worker restarts.
import { createAnswerQueue, statusOf } from "./lib/offlinequeue.js";
import * as api from "./lib/api.js";
import { signedFetch, currentDevice, rotateDeviceKey } from "./lib/devicekey.js";
import { normalizeApiOrigin } from "./lib/hostaccess.js";
import { createScopedState, API_BASE_KEY } from "./lib/state.js";

const POLL_ALARM_NAME = "hca-inbox-poll";
const NOTICE_KEY = "hca_delivery_notice";
export function createBackground(chromeObj, dependencies = {}) {
  const services = { ...api, signedFetch, currentDevice, rotateDeviceKey, ...dependencies };
  const storage = chromeObj.storage.local;
  const queue = createAnswerQueue(storage);
  const states = createScopedState(storage);
  let epoch = 0;
  let activeOrigin = null;
  let networkController = new AbortController();
  let streamTask = null;
  let pollTask = null;
  let pollGeneration = 0;
  let notificationGeneration = 0;
  const configSerial = (async () => {
    await chromeObj.storage.local.setAccessLevel?.({ accessLevel: "TRUSTED_CONTEXTS" });
    const legacyKeys = ["hca_inbox_cache", "hca_last_event_id", "hca_focus_request_id", "hca_answer_queue"];
    const legacy = await storage.get(legacyKeys);
    if (legacy.hca_answer_queue?.length) await storage.set({ [NOTICE_KEY]: "Older saved answers were discarded during this security upgrade because their server and device binding cannot be verified. Review the current inbox." });
    await storage.remove(legacyKeys);
  })();

  async function scope() {
    const capturedEpoch = epoch;
    const controller = networkController;
    await configSerial;
    const value = (await storage.get(API_BASE_KEY))[API_BASE_KEY];
    if (!value) return null;
    const origin = normalizeApiOrigin(value);
    const device = await services.currentDevice();
    if (capturedEpoch !== epoch || controller !== networkController) return null;
    activeOrigin = origin;
    return { origin, deviceId: device.deviceId, epoch: capturedEpoch, signal: controller.signal };
  }
  const isCurrent = (s) => !!s && s.epoch === epoch && s.origin === activeOrigin && !s.signal.aborted;
  function invalidateNetwork() {
    epoch += 1;
    networkController.abort();
    networkController = new AbortController();
    streamTask = null;
    pollTask = null;
    pollGeneration += 1;
  }
  async function badge(state) { await chromeObj.action.setBadgeText({ text: Object.keys(state?.cache || {}).length ? String(Object.keys(state.cache).length) : "" }); }
  async function notice(text) { await storage.set({ [NOTICE_KEY]: text }); }
  async function clearNotifications() {
    notificationGeneration += 1;
    const existing = await chromeObj.notifications.getAll();
    await Promise.all(Object.keys(existing).filter((id) => id.startsWith("hca-")).map((id) => chromeObj.notifications.clear(id)));
  }
  async function notify(item, s) {
    if (!isCurrent(s)) return;
    const generation = notificationGeneration;
    const previews = (await storage.get("hca_notification_previews")).hca_notification_previews === true;
    if (!isCurrent(s) || generation !== notificationGeneration) return;
    const id = `hca-${item.request_id || item.id}`;
    await chromeObj.notifications.create(id, {
      type: "basic", title: previews ? item.title || "A decision is waiting" : "A decision is waiting",
      message: previews ? item.why || "Open Human Collections Agent to review." : "Open Human Collections Agent to review the decision.",
      iconUrl: chromeObj.runtime.getURL("icons/icon128.png"), priority: item.red_gate ? 2 : 0,
    }).catch(() => {});
    if (!isCurrent(s) || generation !== notificationGeneration) await chromeObj.notifications.clear(id);
  }
  async function clearSensitiveState(s, message) {
    await states.clear(s);
    await queue.clear((e) => e.origin === s.origin && e.deviceId === s.deviceId);
    await badge(null);
    await clearNotifications();
    await notice(message);
  }
  async function handleAuthFailure(error, s) {
    if (isCurrent(s) && [401, 403].includes(statusOf(error))) {
      invalidateNetwork();
      await clearSensitiveState(s, "Device access was refused. Cached decisions and queued answers were cleared; register and link this device again.");
      return true;
    }
    return false;
  }
  async function flush(s) {
    if (!isCurrent(s)) return;
    const counts = await queue.flush(async (entry) => {
      try {
        return await services.postAnswer(entry.origin, entry.requestId, entry.body, {
          idempotencyKey: entry.idempotencyKey,
          signal: AbortSignal.any([s.signal, AbortSignal.timeout(30000)]),
        });
      } catch (error) { await handleAuthFailure(error, s); throw error; }
    }, s, { isCurrent: () => isCurrent(s) });
    if (isCurrent(s) && (counts.dropped || counts.stale || counts.rejected)) await notice(`${counts.dropped + counts.stale + counts.rejected} saved answer(s) expired, became stale, were rejected or could not be delivered. Review the current inbox before answering again.`);
  }
  function pollInbox() {
    if (pollTask) return pollTask;
    const generation = ++pollGeneration;
    pollTask = (async () => {
      const s = await scope();
      if (!s) return;
      try {
        await flush(s);
        if (!isCurrent(s)) return;
        const items = await services.fetchInbox(s.origin, { signal: AbortSignal.any([s.signal, AbortSignal.timeout(30000)]) });
        const old = await states.read(s);
        const cache = Object.fromEntries(items.map((item) => [item.request_id || item.id, item]));
        const next = await states.update(s, (state) => ({ ...state, cache }), () => isCurrent(s));
        if (!next) return;
        await badge(next);
        for (const item of items) if (!old.cache[item.request_id || item.id]) await notify(item, s);
      } catch (error) { await handleAuthFailure(error, s); }
    })().finally(() => { if (pollGeneration === generation) pollTask = null; });
    return pollTask;
  }
  async function handleStreamEvent(event, s) {
    if (!isCurrent(s)) return;
    const record = event.data ? services.unwrapStreamEnvelope(event.data) : null;
    if (!record) return;
    if (event.event === "stream.reset" || event.event === "stream.gap") {
      await states.update(s, (previous) => ({ ...previous, cursor: null }), () => isCurrent(s));
      void pollInbox(); return;
    }
    const added = ["request.created", "request.escalated"].includes(event.event);
    const removed = ["request.answered", "request.cancelled", "request.expired"].includes(event.event);
    if (added && !services.isAddressedTo(record, s.deviceId)) return;
    const id = record.request_id || record.id;
    let isNew = false;
    const state = await states.update(s, (previous) => {
      const cache = { ...previous.cache };
      if (added && id) { isNew = !cache[id]; cache[id] = record; }
      if (removed && id) delete cache[id];
      return { ...previous, cache, cursor: event.id || previous.cursor };
    }, () => isCurrent(s));
    if (!state) return;
    await badge(state);
    if (isNew) await notify(record, s);
    if (removed && id) await chromeObj.notifications.clear(`hca-${id}`);
  }
  // The promise is installed before any await, preventing duplicate stream
  // connections during simultaneous startup, alarms and popup messages.
  function connectStream() {
    if (streamTask) return streamTask;
    const controller = networkController;
    streamTask = (async () => {
      const s = await scope();
      if (!s || !isCurrent(s)) return;
      let reader;
      try {
        const state = await states.read(s);
        const response = await services.signedFetch(`${s.origin}/v1/inbox/stream`, {
          signal: s.signal, headers: state.cursor ? { "Last-Event-ID": state.cursor } : {},
        });
        if (!response.ok || !response.body) { const error = new Error(`Stream refused: ${response.status}`); error.status = response.status; throw error; }
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (isCurrent(s)) {
          const { done, value } = await reader.read();
          if (done || !isCurrent(s)) break;
          const parsed = services.parseSSEChunk(buffer, decoder.decode(value, { stream: true }));
          buffer = parsed.buffer;
          if (buffer.length > 1024 * 1024) throw new Error("Oversized inbox event");
          for (const event of parsed.events) {
            if (["stream.reset", "stream.gap"].includes(event.event)) { await handleStreamEvent(event, s); return; }
            if (event.event === "stream.closed") {
              const reason = services.unwrapStreamEnvelope(event.data)?.reason;
              if (["revoked", "unauthorized", "owner_removed", "authorization revoked", "device revoked"].includes(reason)) {
                invalidateNetwork();
                await clearSensitiveState(s, "Device access was revoked. Register and link this device again.");
              }
              return;
            }
            await handleStreamEvent(event, s);
          }
        }
      } catch (error) { await handleAuthFailure(error, s); }
      finally { await reader?.cancel().catch(() => {}); }
    })().finally(() => { if (networkController === controller) streamTask = null; });
    return streamTask;
  }
  function wake() {
    void pollInbox().catch((error) => notice(`Inbox refresh failed: ${error.message}`));
    void connectStream().catch((error) => notice(`Inbox stream failed: ${error.message}`));
  }
  async function configChanged(previousOrigin) {
    // Invalidation occurs synchronously before an old stream can consume more
    // frames or a stale poll can overwrite the new origin's cache.
    invalidateNetwork();
    activeOrigin = null;
    if (previousOrigin) {
      const device = await services.currentDevice();
      try { await states.clear({ origin: normalizeApiOrigin(previousOrigin), deviceId: device.deviceId }); } catch (_) { /* reject obsolete invalid configuration */ }
      const dropped = await queue.clear((entry) => entry.origin === previousOrigin);
      if (dropped) await notice(`${dropped} saved answer(s) were discarded when the API server changed. Review decisions at the new server.`);
    }
    await clearNotifications();
    await badge(null);
    wake();
  }
  async function message(msg) {
    if (msg.type === "hca_dismiss_notice") { await storage.remove(NOTICE_KEY); return {}; }
    if (msg.type === "hca_set_origin") {
      const origin = normalizeApiOrigin(msg.origin);
      if (!await chromeObj.permissions.contains({ origins: [`${origin}/*`] })) throw new Error("Allow access to the API origin first");
      await storage.set({ [API_BASE_KEY]: origin });
      return { origin };
    }
    if (msg.type === "hca_device") {
      const { deviceId, publicKeyPem, createdAt } = await services.currentDevice();
      return { deviceId, publicKeyPem, createdAt };
    }
    if (msg.type === "hca_rotate_device") {
      invalidateNetwork();
      await queue.clear(() => true);
      const all = await storage.get(null);
      await storage.remove(Object.keys(all).filter((key) => key.startsWith("hca_state_v2:") || ["hca_inbox_cache", "hca_last_event_id", "hca_focus_request_id", "hca_answer_queue"].includes(key)));
      await clearNotifications(); await badge(null);
      const device = await services.rotateDeviceKey();
      await storage.set({ hca_device_generation: globalThis.crypto.randomUUID() });
      await notice("Device key rotated. Revoke the old server registration, register the new public key and sign in again before answering.");
      return { deviceId: device.deviceId, publicKeyPem: device.publicKeyPem, createdAt: device.createdAt };
    }
    const s = await scope();
    if (msg.type === "hca_snapshot") {
      const pending = await queue.snapshot();
      const state = s ? await states.read(s) : { cache: {} };
      if (pending.dropped) await notice(`${pending.dropped} saved answer(s) expired or reached the delivery attempt limit.`);
      return { ...state, origin: s?.origin, deviceId: s?.deviceId, queue: pending.queue.filter((e) => e.origin === s?.origin && e.deviceId === s?.deviceId), notice: (await storage.get(NOTICE_KEY))[NOTICE_KEY] || "" };
    }
    if (msg.type === "hca_submit_answer") {
      if (!s || msg.origin !== s.origin || msg.deviceId !== s.deviceId || !isCurrent(s)) throw new Error("API configuration changed. Reopen the decision before answering.");
      const state = await states.read(s);
      const item = state.cache[msg.requestId];
      if (!item || item.control !== msg.control) throw new Error("This request is no longer in the current inbox. Refresh before answering.");
      const body = services.buildAnswerPayload({ control: item.control, selection: msg.selection });
      const entry = await queue.enqueue({ requestId: msg.requestId, body, origin: s.origin, deviceId: s.deviceId, deadlineAt: item.expiry_policy === "hold" ? null : item.deadline_at });
      if (!isCurrent(s)) { await queue.clear((e) => e.entryId === entry.entryId); throw new Error("API configuration changed before submission. Review the new inbox."); }
      const next = await states.update(s, (state) => { const cache = { ...state.cache }; delete cache[msg.requestId]; return { ...state, cache }; }, () => isCurrent(s));
      await badge(next);
      void flush(s).catch((error) => notice(`Saved answer delivery failed: ${error.message}`));
      return { queued: true, entryId: entry.entryId };
    }
    if (msg.type === "hca_flush_queue") { if (s) void flush(s).catch((error) => notice(`Saved answer delivery failed: ${error.message}`)); return { ok: true }; }
    throw new Error("Unknown extension request");
  }
  chromeObj.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (sender.id !== chromeObj.runtime.id || sender.url && !sender.url.startsWith(chromeObj.runtime.getURL(""))) return false;
    message(msg).then((result) => sendResponse({ ok: true, ...result }), (error) => sendResponse({ ok: false, error: error.message }));
    return true;
  });
  chromeObj.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[API_BASE_KEY] && changes[API_BASE_KEY].newValue !== changes[API_BASE_KEY].oldValue) void configChanged(changes[API_BASE_KEY].oldValue);
    if (area === "local" && changes.hca_notification_previews && changes.hca_notification_previews.newValue !== true) void clearNotifications();
  });
  chromeObj.runtime.onInstalled.addListener(wake);
  chromeObj.runtime.onStartup.addListener(wake);
  chromeObj.alarms.onAlarm.addListener((alarm) => { if (alarm.name === POLL_ALARM_NAME) wake(); });
  chromeObj.notifications.onClicked.addListener(async (id) => {
    if (!id.startsWith("hca-")) return;
    const s = await scope();
    if (!s) return;
    const requestId = id.slice(4);
    await states.update(s, (state) => ({ ...state, focusId: state.cache[requestId] ? requestId : null }), () => isCurrent(s));
    await chromeObj.action.openPopup().catch(() => {});
  });
  // Ensure a missing alarm is restored whenever Chrome starts the worker.
  void chromeObj.alarms.create(POLL_ALARM_NAME, { periodInMinutes: 1 });
  return { wake, pollInbox, connectStream, message, configChanged, handleStreamEvent, scope };
}
if (globalThis.chrome?.runtime) createBackground(globalThis.chrome).wake();
