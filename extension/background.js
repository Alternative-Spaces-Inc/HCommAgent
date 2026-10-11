// Background service worker: the browser overlay surface's SSE connection
// plus the chrome.alarms fallback named in docs/decisions/DECISIONS_20260929.md
// decision 1. MV3 service workers are suspended after roughly 30 seconds
// idle, so the live SSE connection is a best-effort fast path; the alarm is
// what actually guarantees delivery is never missed for longer than one
// poll interval, including across a suspend/wake cycle that drops the
// stream mid-connection.

import { flushQueue, readQueue } from "./lib/offlinequeue.js";
import { fetchInbox, postAnswer, parseSSEChunk, unwrapStreamEnvelope, isAddressedTo, SURFACE } from "./lib/api.js";
import { signedFetch, currentDevice } from "./lib/devicekey.js";

const POLL_ALARM_NAME = "hca-inbox-poll";
const POLL_INTERVAL_MINUTES = 1;
const CACHE_KEY = "hca_inbox_cache";
const API_BASE_KEY = "hca_api_base";

let streamAbortController = null;
// HCA-P20 Day 5: true while a stream fetch is being read. The poll alarm used
// to call connectStream() on every tick, which opened a fresh stream while the
// proxy kept the previous one alive: the live metrics page showed 46 streams
// from one device on one instance. The alarm now reconnects only when no
// stream is live; the server also caps streams per device and closes the
// oldest with event stream.closed, reason superseded.
let streamActive = false;

async function getApiBase() {
  const stored = await chrome.storage.local.get(API_BASE_KEY);
  return stored[API_BASE_KEY] || null;
}

// HCA-P20: last SSE id seen on the stream, sent as Last-Event-ID on the
// next connect so the server replays the envelopes missed in between
// (24 h or 1,000 events); the poll alarm remains the fallback beyond that.
const LAST_EVENT_ID_KEY = "hca_last_event_id";

async function getLastEventId() {
  const stored = await chrome.storage.local.get(LAST_EVENT_ID_KEY);
  return stored[LAST_EVENT_ID_KEY] || null;
}

async function setLastEventId(id) {
  if (id) await chrome.storage.local.set({ [LAST_EVENT_ID_KEY]: String(id) });
}

async function getCache() {
  const stored = await chrome.storage.local.get(CACHE_KEY);
  return stored[CACHE_KEY] || {};
}

async function setCache(cache) {
  await chrome.storage.local.set({ [CACHE_KEY]: cache });
  await chrome.action.setBadgeText({ text: Object.keys(cache).length ? String(Object.keys(cache).length) : "" });
}

function requestIdOf(item) {
  // GET /v1/inbox returns CollectionRequest objects (api/openapi.yaml);
  // the request_id is carried as a sibling field the API adds on top of
  // the CollectionRequest shape (see api/app/requests_api.py's
  // _ask_to_collection_request plus the request_id merged in on create/
  // publish). Fall back defensively if that ever changes shape.
  return item.request_id || item.id;
}

async function notifyNewAsk(item) {
  const id = requestIdOf(item);
  try {
    await chrome.notifications.create(`hca-${id}`, {
      type: "basic",
      title: item.title || "A decision is waiting",
      message: item.why || "Open the Human Collections Agent popup to answer.",
      // Chrome requires iconUrl for type basic; without it create() fails
      // with "required properties are missing" (seen on the card 2026-10-02).
      iconUrl: chrome.runtime.getURL("icons/icon128.png"),
      priority: item.red_gate ? 2 : 0,
    });
  } catch (e) {
    // the badge count still reflects the pending ask either way.
    console.warn("HCA notification failed", e);
  }
}

async function reconcileWithInbox(apiBase) {
  const items = await fetchInbox(apiBase);
  const cache = await getCache();
  const nextCache = {};
  for (const item of items) {
    const id = requestIdOf(item);
    nextCache[id] = item;
    if (!cache[id]) {
      await notifyNewAsk(item);
    }
  }
  await setCache(nextCache);
}

// HCA-P12: replay answers the popup saved while the API was unreachable.
// Runs before every poll and on every stream (re)connect, and on demand
// when the popup has just queued one. Signing happens here, at send time.
// The guard is taken synchronously, before the first await: the poll
// alarm and the stream reconnect both call this within the same tick when
// the network returns, and a check that waited on storage first let both
// through (2026-10-03 proof take 3: one 201, one 500 on the same answer).
let flushing = false;
async function flushOfflineAnswers(apiBase) {
  if (flushing) return;
  flushing = true;
  try {
    const pending = await readQueue();
    if (!pending.length) return;
    const counts = await flushQueue((requestId, body) => postAnswer(apiBase, requestId, body));
    if (counts.sent || counts.stale || counts.dropped) {
      console.info("HCA offline queue flushed", counts);
    }
    if (counts.dropped) {
      await chrome.notifications.create("hca-queue-dropped", {
        type: "basic",
        title: "An offline answer could not be delivered",
        message: "The server kept refusing it. Open the popup to answer again if the ask is still pending.",
        iconUrl: chrome.runtime.getURL("icons/icon128.png"),
      });
    }
  } catch (e) {
    console.warn("HCA offline queue flush failed", e);
  } finally {
    flushing = false;
  }
}

async function pollInbox() {
  const apiBase = await getApiBase();
  if (!apiBase) return;
  await flushOfflineAnswers(apiBase);
  try {
    await reconcileWithInbox(apiBase);
  } catch (e) {
    console.warn("HCA poll failed", e);
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "hca_flush_queue") {
    getApiBase().then((apiBase) => apiBase && flushOfflineAnswers(apiBase));
    // Reply so the popup's sendMessage promise settles cleanly instead of
    // Chrome logging a dropped reply on the extensions card.
    sendResponse({ ok: true });
  }
  return false;
});

async function connectStream() {
  const apiBase = await getApiBase();
  if (!apiBase) return;
  await flushOfflineAnswers(apiBase);

  if (streamAbortController) {
    streamAbortController.abort();
  }
  streamAbortController = new AbortController();

  try {
    const lastEventId = await getLastEventId();
    const headers = lastEventId ? { "Last-Event-ID": lastEventId } : {};
    const resp = await signedFetch(`${apiBase}/v1/inbox/stream`, { signal: streamAbortController.signal, headers });
    if (!resp.ok || !resp.body) {
      throw new Error(`stream connect failed: ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    streamActive = true;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunkText = decoder.decode(value, { stream: true });
      const parsed = parseSSEChunk(buffer, chunkText);
      buffer = parsed.buffer;
      let superseded = false;
      for (const evt of parsed.events) {
        if (evt.event === "stream.closed") {
          // the server closed this stream (device revoked, or a newer
          // stream of ours took its slot); do not reopen from here, the
          // next alarm tick or the newer stream carries on
          superseded = true;
          continue;
        }
        await handleStreamEvent(evt);
        if (evt.id) await setLastEventId(evt.id);
      }
      if (superseded) break;
    }
  } catch (e) {
    // The stream drops when the service worker suspends, on a network
    // blip, or when the tab-less background context is torn down; the
    // poll alarm below is the fallback that guarantees we do not miss
    // anything for longer than POLL_INTERVAL_MINUTES.
    console.warn("HCA stream ended", e);
  } finally {
    streamActive = false;
  }
}

async function handleStreamEvent(evt) {
  const apiBase = await getApiBase();
  if (!apiBase) return;
  const cache = await getCache();

  // evt.data is the raw parsed JSON of the SSE data: line, which is the
  // full publish envelope ({event, data, timestamp}), not the record
  // itself; unwrap to the actual CollectionRequest or AnswerRecord.
  const record = evt.data ? unwrapStreamEnvelope(evt.data) : null;
  if (!record) return;

  if (evt.event === "request.created" || evt.event === "request.escalated") {
    // HCA-P14: show only envelopes addressed to this device; the poll applies
    // the same server-side rule, so a later rank still sees the ask once its
    // window has opened even if the escalated event was missed.
    let deviceId = null;
    try { deviceId = (await currentDevice()).deviceId; } catch (e) { deviceId = null; }
    if (!isAddressedTo(record, deviceId)) return;
    const id = requestIdOf(record);
    if (id && !cache[id]) {
      cache[id] = record;
      await setCache(cache);
      await notifyNewAsk(record);
    }
    return;
  }

  if (["request.answered", "request.cancelled", "request.expired"].includes(evt.event)) {
    const id = record.request_id;
    if (id && cache[id]) {
      delete cache[id];
      await setCache(cache);
      await chrome.notifications.clear(`hca-${id}`);
    }
  }
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: POLL_INTERVAL_MINUTES });
  pollInbox();
  connectStream();
});

chrome.runtime.onStartup.addListener(() => {
  pollInbox();
  connectStream();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM_NAME) {
    pollInbox();
    // Re-establish the stream only when none is live: if the service worker
    // was suspended the previous reader is gone (streamActive reset with the
    // module), so this is a fresh connect; while a stream is being read,
    // opening another would leave the old one hanging on the server.
    if (!streamActive) connectStream();
  }
});

chrome.notifications.onClicked.addListener(async (notificationId) => {
  if (!notificationId.startsWith("hca-")) return;
  const requestId = notificationId.slice("hca-".length);
  await chrome.storage.local.set({ hca_focus_request_id: requestId });
  try {
    await chrome.action.openPopup();
  } catch (e) {
    console.warn("HCA could not open popup from notification", e);
  }
});

export const __test_only = { SURFACE, requestIdOf };
