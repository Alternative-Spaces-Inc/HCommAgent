// Actual Chromium DOM, IndexedDB and worker-context tests. No Google account,
// public server or secret is needed. Run npm run test:browser after installing
// Playwright and its Chromium browser (see TESTING.md).
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir } from "node:fs/promises";
import { dirname, resolve, extname } from "node:path";
import { fileURLToPath } from "node:url";
const { chromium } = await import(process.env.HCA_PLAYWRIGHT_MODULE || "playwright");
const extension = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const workerScript = `import {loadOrCreateDeviceKey,indexedDeviceStorage} from '/lib/devicekey.js';
const chromeMock={storage:{local:{get:async()=>({}),remove:async()=>{}}}};
loadOrCreateDeviceKey(indexedDeviceStorage(indexedDB,chromeMock)).then(async key=>{
let exportRefused=false;try{await crypto.subtle.exportKey('jwk',key.privateKey)}catch{exportRefused=true}
postMessage({deviceId:key.deviceId,exportRefused});}).catch(error=>postMessage({error:error.message}));`;
const server = createServer(async (req, response) => {
  try {
    if (req.url === "/key-worker.js") { response.setHeader("Content-Type", "text/javascript"); response.end(workerScript); return; }
    const path = resolve(extension, `.${new URL(req.url, "http://localhost").pathname}`);
    if (!path.startsWith(`${extension}/`)) { response.writeHead(404); response.end(); return; }
    response.setHeader("Content-Type", extname(path) === ".js" ? "text/javascript" : "text/html");
    response.end(await readFile(path));
  } catch { response.writeHead(404); response.end(); }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  browser = await chromium.launch({ headless: true, executablePath: process.env.HCA_CHROMIUM_PATH || undefined });
  const context = await browser.newContext();
  const first = await context.newPage(), second = await context.newPage();
  await Promise.all([first.goto(`${base}/options.html`), second.goto(`${base}/options.html`)]);
  const initialize = (page) => page.evaluate(async () => {
    const { loadOrCreateDeviceKey, indexedDeviceStorage } = await import("/lib/devicekey.js");
    const chromeMock = { storage: { local: { get: async () => ({}), remove: async () => {} } } };
    const keys = await Promise.all(Array.from({ length: 5 }, () => loadOrCreateDeviceKey(indexedDeviceStorage(indexedDB, chromeMock))));
    return keys.map((key) => ({ deviceId: key.deviceId, extractable: key.privateKey.extractable }));
  });
  const keys = (await Promise.all([initialize(first), initialize(second)])).flat();
  assert.equal(new Set(keys.map((key) => key.deviceId)).size, 1);
  assert.ok(keys.every((key) => !key.extractable));
  const workerKey = () => first.evaluate(() => new Promise((done, fail) => {
    const worker = new Worker("/key-worker.js", { type: "module" });
    worker.onmessage = ({ data }) => { worker.terminate(); done(data); }; worker.onerror = fail;
  }));
  for (let i = 0; i < 2; i++) {
    const key = await workerKey(); assert.equal(key.deviceId, keys[0].deviceId); assert.equal(key.exportRefused, true);
  }
  console.log("PASS: actual IndexedDB atomic initialization across tabs; durable non-extractable key across worker restarts");
  for (const control of ["radio", "checkbox", "approve_deny", "dropdown", "chat", "confirm_rows"]) {
    const page = await context.newPage();
    await page.addInitScript(({ control }) => {
      globalThis.submitted = [];
      const item = { request_id: "r1", title: "Approve the sample workflow", why: "Synthetic decision context", control,
        options_or_fields: [{ id: "one", label: "First", description: "A complete readable explanation" }, { id: "deny", label: "Deny" }],
        suggested: { option_id: "one", why: "Example suggestion" }, red_gate: { resource: "Sample database", change: "Apply migration", impact: "Changes schema", exact_command: "safe-demo --example", rollback: "Restore demo snapshot" } };
      globalThis.chrome = { storage: { onChanged: { addListener() {} } }, runtime: { async sendMessage(message) {
        if (message.type === "hca_snapshot") return { ok: true, origin: "https://api.example", deviceId: "dev-test", cache: { r1: item }, queue: [], notice: "" };
        if (message.type === "hca_submit_answer") { globalThis.submitted.push(message); await new Promise((done) => { globalThis.finishSubmit = done; }); return { ok: true, queued: true }; }
      } } };
    }, { control });
    await page.goto(`${base}/popup.html`);
    await page.getByRole("heading", { name: "Approve the sample workflow" }).waitFor();
    assert.equal(await page.getByText("Sample database", { exact: true }).count(), 1);
    if (process.env.HCA_SCREENSHOT_DIR && control === "approve_deny") {
      await mkdir(process.env.HCA_SCREENSHOT_DIR, { recursive: true });
      await page.setViewportSize({ width: 380, height: 950 });
      await page.screenshot({ path: resolve(process.env.HCA_SCREENSHOT_DIR, "decision-review.png"), fullPage: true });
    }
    assert.equal(await page.getByText("safe-demo --example", { exact: true }).count(), 1);
    if (control === "radio") assert.equal(await page.locator('input[type="radio"]:checked').count(), 0);
    if (["checkbox", "confirm_rows"].includes(control)) assert.equal(await page.locator('fieldset input:checked').count(), 0);
    const submit = control === "approve_deny" ? page.getByRole("button", { name: /First/ }) : page.getByRole("button", { name: "Submit response" });
    await submit.click();
    await page.getByRole("alert").filter({ hasText: "Review the decision context" }).waitFor();
    assert.equal(await page.evaluate(() => submitted.length), 0);
    await page.getByRole("checkbox", { name: /I have reviewed/ }).check();
    if (control === "radio") await page.getByRole("radio", { name: /First/ }).check();
    if (["checkbox", "confirm_rows"].includes(control)) await page.getByRole("checkbox", { name: /First/ }).check();
    if (control === "dropdown") {
      await page.getByRole("searchbox", { name: "Search choices" }).fill("First");
      await page.getByRole("combobox", { name: "Selection" }).selectOption("one");
    }
    if (control === "chat") await page.getByRole("textbox", { name: /Message/ }).fill("Proceed with synthetic data");
    await submit.click();
    assert.equal(await submit.isDisabled(), true);
    await page.waitForFunction(() => submitted.length === 1);
    const sent = await page.evaluate(() => submitted[0]);
    assert.equal(sent.control, control); assert.equal(sent.origin, "https://api.example"); assert.equal(sent.deviceId, "dev-test");
    assert.deepEqual(sent.selection, ["checkbox", "confirm_rows"].includes(control) ? ["one"] : control === "chat" ? "Proceed with synthetic data" : "one");
    await page.evaluate(() => finishSubmit());
    await page.locator("section.card").waitFor({ state: "detached" });
    await page.close();
  }
  console.log("PASS: six controls through actual popup; full context, explicit selection, red-gate review, live errors and duplicate-submit lock");
} finally { await browser?.close(); await new Promise((done) => server.close(done)); }
