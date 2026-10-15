// Shared harness for the P23 browser proofs (docs/plans/HCA_P23_EXTENSION_HARDENING_v1.md
// section 4). The actual background.js module runs inside a real Chromium page over a
// chrome mock whose storage, alarms, notifications, tabs and action calls are recorded;
// network goes through real fetch to an https origin that Playwright routes, so aborts,
// stream closure and malformed bytes are exercised on real browser networking. A "worker
// restart" is a second createBackground over the same storage, which is what Chrome does
// when it terminates and revives a service worker.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";

export const ORIGIN = "https://api.example";
export const extension = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const types = { ".js": "text/javascript", ".html": "text/html", ".png": "image/png", ".json": "application/json" };

export async function serveExtension() {
  const server = createServer(async (req, response) => {
    try {
      const pathname = new URL(req.url, "http://localhost").pathname;
      if (pathname === "/bg-host.html") { response.setHeader("Content-Type", "text/html"); response.end("<!doctype html><title>bg host</title>"); return; }
      const path = resolve(extension, `.${pathname}`);
      if (!path.startsWith(`${extension}/`)) { response.writeHead(404); response.end(); return; }
      response.setHeader("Content-Type", types[extname(path)] || "application/octet-stream");
      response.end(await readFile(path));
    } catch { response.writeHead(404); response.end(); }
  });
  await new Promise((done) => server.listen(0, "127.0.0.1", done));
  return { base: `http://127.0.0.1:${server.address().port}`, close: () => new Promise((done) => server.close(done)) };
}

// Installed before any script: an in memory chrome with recorded calls. Storage is shared
// between pages of one context through BroadcastChannel so two popups see one worker store.
export const chromeMockScript = `(() => {
  const calls = []; const record = (name, ...args) => { calls.push({ name, args }); };
  const store = globalThis.__hcaStore || (globalThis.__hcaStore = {});
  const listeners = { changed: [], alarm: [], clicked: [], removed: [], message: [] };
  const storage = {
    local: {
      async get(keys) { const names = keys == null ? Object.keys(store) : Array.isArray(keys) ? keys : typeof keys === "string" ? [keys] : Object.keys(keys); const out = {}; for (const k of names) if (k in store) out[k] = structuredClone(store[k]); return out; },
      async set(values) { const changes = {}; for (const [k, v] of Object.entries(values)) { changes[k] = { oldValue: store[k], newValue: v }; store[k] = structuredClone(v); } listeners.changed.forEach((fn) => fn(changes, "local")); },
      async remove(keys) { for (const k of Array.isArray(keys) ? keys : [keys]) delete store[k]; listeners.changed.forEach((fn) => fn({}, "local")); },
      async clear() { for (const k of Object.keys(store)) delete store[k]; },
      async setAccessLevel() {},
    },
    onChanged: { addListener(fn) { listeners.changed.push(fn); } },
  };
  const tabs = new Map(); let nextTab = 100;
  globalThis.__hca = { calls, listeners, store, tabs, openPopupRejects: false, permissionGranted: true };
  globalThis.chrome = {
    storage,
    alarms: { async create(name, info) { record("alarms.create", name, info); }, onAlarm: { addListener(fn) { listeners.alarm.push(fn); } } },
    notifications: { async create(id, options) { record("notifications.create", id, options); globalThis.__hca.notifications = { ...(globalThis.__hca.notifications || {}), [id]: options }; return id; }, async clear(id) { record("notifications.clear", id); const n = globalThis.__hca.notifications || {}; delete n[id]; return true; }, async getAll() { return { ...(globalThis.__hca.notifications || {}) }; }, onClicked: { addListener(fn) { listeners.clicked.push(fn); } }, onClosed: { addListener() {} } },
    action: { async setBadgeText(d) { record("action.setBadgeText", d); }, async openPopup() { record("action.openPopup"); if (globalThis.__hca.openPopupRejects) throw new Error("Could not find an active browser window."); } },
    tabs: { async create({ url }) { const id = nextTab++; tabs.set(id, { id, url, active: true }); record("tabs.create", url); return { id, url }; }, async get(id) { if (!tabs.has(id)) throw new Error("No tab with id: " + id); return tabs.get(id); }, async update(id, props) { record("tabs.update", id, props); Object.assign(tabs.get(id), props); return tabs.get(id); }, onRemoved: { addListener(fn) { listeners.removed.push(fn); } } },
    windows: { async update(id, props) { record("windows.update", id, props); } },
    permissions: { async contains() { return globalThis.__hca.permissionGranted; }, async request() { record("permissions.request"); return globalThis.__hca.permissionGranted; } },
  };
  // runtime is attached after background.js is imported so the module does not self start.
  globalThis.__hcaRuntime = { getURL: (p) => "chrome-extension://test/" + p, onInstalled: { addListener() {} }, onStartup: { addListener() {} }, onMessage: { addListener(fn) { listeners.message.push(fn); } }, async sendMessage(m) { for (const fn of listeners.message) { const r = await new Promise((done) => { const sync = fn(m, {}, done); if (sync !== true) done(undefined); }); if (r !== undefined) return r; } return undefined; } };
})();`;

// Boots a page hosting the real background module and returns helpers bound to it.
export async function bootWorker(context, base, { origin = ORIGIN } = {}) {
  const page = await context.newPage();
  await page.addInitScript(chromeMockScript);
  await page.goto(`${base}/bg-host.html`);
  await page.evaluate(async ({ origin }) => {
    const mod = await import("/background.js");
    globalThis.chrome.runtime = globalThis.__hcaRuntime;
    globalThis.__mod = mod;
    await chrome.storage.local.set({ hca_api_base: origin });
    globalThis.bg = mod.createBackground(chrome);
  }, { origin });
  return {
    page,
    restart: () => page.evaluate(() => { globalThis.bg = globalThis.__mod.createBackground(chrome); }),
    wake: () => page.evaluate(() => bg.wake()),
    message: (msg) => page.evaluate((m) => chrome.runtime.sendMessage(m), msg),
    calls: (name) => page.evaluate((n) => __hca.calls.filter((c) => !n || c.name === n), name),
    store: () => page.evaluate(() => structuredClone(__hca.store)),
    queue: () => page.evaluate(() => structuredClone(__hca.store.hca_answer_queue_v2 || [])),
    deviceId: async () => (await page.evaluate(() => chrome.runtime.sendMessage({ type: "hca_device" }))).deviceId,
  };
}

export const sampleItem = (id, extra = {}) => ({ request_id: id, title: `Question ${id}`, why: "Synthetic context", control: "approve_deny",
  options_or_fields: [{ id: "approve", label: "Approve" }, { id: "deny", label: "Deny" }], suggested: { option_id: "approve", why: "Example" }, expiry_policy: "hold", ...extra });
export const answerBody = (deviceId) => ({ control: "approve_deny", selection: "approve", answered_by: "test", device: deviceId });
export const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
