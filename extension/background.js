// Background service worker: the browser overlay surface's SSE connection
// plus the chrome.alarms fallback named in docs/decisions/DECISIONS_20260929.md
// decision 1. MV3 service workers are suspended after roughly 30 seconds
// idle, so the live SSE connection is a best-effort fast path; the alarm is
// what actually guarantees delivery is never missed for longer than one
// poll interval, including across a suspend/wake cycle that drops the
// stream mid-connection.

import { flushQueue, readQueue } from "./lib/offlinequeue.js";
import { fetchInbox, postAnswer, parseSSEChunk, unwrapStreamEnvelope, SURFACE } from "./lib/api.js";
import { signedFetch } from "./lib/devicekey.js";

const POLL_ALARM_NAME = "hca-inbox-poll";
const POLL_INTERVAL_MINUTES = 1;
const CACHE_KEY = "hca_inbox_cache";
const API_BASE_KEY = "hca_api_base";

let streamAbortController = null;

async function getApiBase() {
  const stored = await chrome.storage.local.get(API_BASE_KEY);
  return stored[API_BASE_KEY] || null;
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
let flushing = false;
async function flushOfflineAnswers(apiBase) {
  if (flushing) return;
  const pending = await readQueue();
  if (!pending.length) return;
  flushing = true;
  try {
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

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "hca_flush_queue") {
    getApiBase().then((apiBase) => apiBase && flushOfflineAnswers(apiBase));
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
    const resp = await signedFetch(`${apiBase}/v1/inbox/stream`, { signal: streamAbortController.signal });
    if (!resp.ok || !resp.body) {
      throw new Error(`stream connect failed: ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const chunkText = decoder.decode(value, { stream: true });
      const parsed = parseSSEChunk(buffer, chunkText);
      buffer = parsed.buffer;
      for (const evt of parsed.events) {
        await handleStreamEvent(evt);
      }
    }
  } catch (e) {
    // The stream drops when the service worker suspends, on a network
    // blip, or when the tab-less background context is torn down; the
    // poll alarm below is the fallback that guarantees we do not miss
    // anything for longer than POLL_INTERVAL_MINUTES.
    console.warn("HCA stream ended", e);
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

  if (evt.event === "request.created") {
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
    // Re-establish the stream too: if the service worker was suspended,
    // any previous fetch reader is already gone, so this is a normal
    // fresh connect rather than a true reconnect.
    connectStream();
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
