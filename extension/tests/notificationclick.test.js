// HCA-P23 Day 1: the notification click path is deterministic. Popup first; when Chrome
// refuses, a full tab decision page for that one ask; a second click reuses the tab; a tab
// the user closed is forgotten and a fresh one opens. Nothing is cleared on notification
// close, so the ask stays in the inbox.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createBackground, DECISION_TAB_KEY } from "../background.js";
import { API_BASE_KEY } from "../lib/state.js";

const origin = "https://api.example";
const deviceId = "dev-test";
const event = () => { const listeners = []; return { addListener(fn) { listeners.push(fn); }, emit(...args) { for (const fn of listeners) fn(...args); } }; };

function mockChrome({ popupRefused = true, initial = {} } = {}) {
  const data = { [API_BASE_KEY]: origin, ...initial };
  const onChanged = event();
  const tabs = new Map();
  let nextTab = 100;
  const calls = { openPopup: 0, created: [], updated: [], focused: [] };
  return {
    data, calls, tabs,
    storage: { onChanged, local: {
      async get(keys) { if (keys === null) return structuredClone(data); if (!Array.isArray(keys)) keys = [keys]; return Object.fromEntries(keys.map((key) => [key, structuredClone(data[key])])); },
      async set(value) { Object.assign(data, structuredClone(value)); onChanged.emit({}, "local"); },
      async remove(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) delete data[key]; },
      async setAccessLevel() {},
    } },
    runtime: { id: "test-extension", getURL: (path) => `chrome-extension://test-extension/${path}`, onMessage: event(), onInstalled: event(), onStartup: event() },
    notifications: { onClicked: event(), async create() {}, async clear() {}, async getAll() { return {}; } },
    action: { async setBadgeText() {}, async openPopup() { calls.openPopup += 1; if (popupRefused) throw new Error("Could not find an active browser window."); } },
    alarms: { onAlarm: event(), async create() {} },
    permissions: { async contains() { return true; } },
    tabs: {
      onRemoved: event(),
      async create({ url }) { const tab = { id: nextTab++, url, windowId: 7, active: true }; tabs.set(tab.id, tab); calls.created.push(url); return tab; },
      async get(id) { if (!tabs.has(id)) throw new Error(`No tab with id: ${id}.`); return tabs.get(id); },
      async update(id, props) { const tab = tabs.get(id); Object.assign(tab, props); calls.updated.push([id, props]); return tab; },
    },
    windows: { async update(id, props) { calls.focused.push([id, props]); } },
  };
}
const deps = { currentDevice: async () => ({ deviceId }), fetchInbox: async () => [], postAnswer: async () => ({}), signedFetch: async () => ({ ok: true, body: new ReadableStream({ start(c) { c.close(); } }) }) };
const decisionUrl = (id) => `chrome-extension://test-extension/decision.html?request=${id}`;

test("popup accepted: no tab is opened", async () => {
  const chrome = mockChrome({ popupRefused: false });
  const worker = createBackground(chrome, deps);
  const result = await worker.notificationClicked("hca-r1");
  assert.deepEqual(result, { surface: "popup" });
  assert.equal(chrome.calls.openPopup, 1);
  assert.deepEqual(chrome.calls.created, []);
});

test("popup refused: a decision tab opens for that one ask and its id is remembered", async () => {
  const chrome = mockChrome();
  const worker = createBackground(chrome, deps);
  const result = await worker.notificationClicked("hca-r1");
  assert.deepEqual(result, { surface: "tab", tabId: 100, reused: false });
  assert.deepEqual(chrome.calls.created, [decisionUrl("r1")]);
  assert.equal(chrome.data[DECISION_TAB_KEY], 100);
});

test("second click while the decision tab exists focuses it instead of opening another", async () => {
  const chrome = mockChrome();
  const worker = createBackground(chrome, deps);
  await worker.notificationClicked("hca-r1");
  const result = await worker.notificationClicked("hca-r2");
  assert.deepEqual(result, { surface: "tab", tabId: 100, reused: true });
  assert.equal(chrome.calls.created.length, 1, "no second tab");
  assert.deepEqual(chrome.calls.updated.at(-1), [100, { active: true, url: decisionUrl("r2") }]);
  assert.deepEqual(chrome.calls.focused.at(-1), [7, { focused: true }]);
});

test("a remembered tab the user closed is forgotten and a fresh tab opens", async () => {
  const chrome = mockChrome({ initial: { [DECISION_TAB_KEY]: 55 } });
  const worker = createBackground(chrome, deps);
  const result = await worker.notificationClicked("hca-r1");
  assert.deepEqual(result, { surface: "tab", tabId: 100, reused: false });
  assert.equal(chrome.data[DECISION_TAB_KEY], 100);
});

test("closing the decision tab clears the remembered id", async () => {
  const chrome = mockChrome();
  createBackground(chrome, deps);
  await chrome.storage.local.set({ [DECISION_TAB_KEY]: 100 });
  chrome.tabs.onRemoved.emit(100);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(chrome.data[DECISION_TAB_KEY], undefined);
  chrome.tabs.onRemoved.emit(999);
  await new Promise((resolve) => setImmediate(resolve));
});

test("ids that are not ours, and an unconfigured server, open nothing", async () => {
  const chrome = mockChrome({ initial: { [API_BASE_KEY]: undefined } });
  delete chrome.data[API_BASE_KEY];
  const worker = createBackground(chrome, deps);
  assert.equal(await worker.notificationClicked("other-1"), null);
  assert.equal(await worker.notificationClicked("hca-r1"), null);
  assert.deepEqual(chrome.calls.created, []);
});

test("the request id is URL encoded in the decision page address", async () => {
  const chrome = mockChrome();
  const worker = createBackground(chrome, deps);
  await worker.notificationClicked("hca-a b/c");
  assert.deepEqual(chrome.calls.created, [`chrome-extension://test-extension/decision.html?request=${encodeURIComponent("a b/c")}`]);
});
