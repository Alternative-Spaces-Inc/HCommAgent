// Scenarios 7 (permission denial), 9 (mobile first layout) and 4 (duplicate clicks, the
// existing six control run in tests/browser.integration.js) of plan section 4.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { ORIGIN, extension, chromeMockScript, sampleItem } from "./harness.js";

export async function permissionDenial(context, base) {
  const requests = [];
  await context.route(`${ORIGIN}/**`, (route) => { requests.push(route.request().url()); return route.fulfill({ status: 200, contentType: "application/json", body: "{}" }); });
  const page = await context.newPage();
  await page.addInitScript(chromeMockScript);
  await page.addInitScript(() => { __hca.permissionGranted = false; globalThis.chrome.runtime = globalThis.__hcaRuntime; chrome.runtime.sendMessage = async (m) => { __hca.calls.push({ name: "runtime.sendMessage", args: [m] }); return m.type === "hca_device" ? { deviceId: "dev-test", publicKeyPem: "pem", createdAt: "" } : { ok: true }; }; });
  await page.goto(`${base}/options.html`);
  await page.getByRole("textbox", { name: /API/i }).or(page.locator("#api-base")).first().fill(ORIGIN);
  await page.locator("#save").click();
  await page.locator("#host-status").filter({ hasText: /refused/ }).waitFor();
  const status = await page.locator("#host-status").textContent();
  assert.match(status, /Access to api\.example was refused; the URL was not saved\./);
  const messages = await page.evaluate(() => __hca.calls.filter((c) => c.name === "runtime.sendMessage").map((c) => c.args[0].type));
  assert.ok(!messages.includes("hca_set_origin"), "origin not saved without host access");
  assert.equal(await page.evaluate(() => __hca.store.hca_api_base), undefined);
  assert.equal(requests.length, 0, "no signed request left the extension");
  await page.close(); await context.unroute(`${ORIGIN}/**`);
  console.log("PASS: permission denial, options page shows the host access error, nothing saved, no request sent");
}

export async function mobileFirstLayout(context, base) {
  const lines = Array.from({ length: 40 }, (_, i) => `Line ${i + 1}: a long paragraph of decision context that must scroll inside the card rather than push the actions off the screen.`);
  const item = sampleItem("r1", { title: "Retry the failed slice or roll back the last two slices of work?", why: lines.join(" "), context: { evidence: lines.slice(0, 10) } });
  const snapshot = { ok: true, origin: ORIGIN, deviceId: "dev", cache: { r1: item }, queue: [], notice: "", focusId: "r1" };
  const results = [];
  for (const [file, width, height, query] of [["popup.html", 360, 560, ""], ["decision.html", 375, 667, "?request=r1"]]) {
    const page = await context.newPage();
    await page.setViewportSize({ width, height });
    await page.addInitScript((snap) => {
      globalThis.chrome = { runtime: { sendMessage: async (m) => (m.type === "hca_snapshot" ? snap : { ok: true }) }, storage: { onChanged: { addListener() {} } } };
    }, snapshot);
    await page.goto(`${base}/${file}${query}`);
    await page.locator(".card-actions button").first().waitFor();
    const m = await page.evaluate(() => {
      const bar = document.querySelector(".card-actions").getBoundingClientRect();
      const body = document.querySelector(".card-body");
      const title = document.querySelector("h2").getBoundingClientRect();
      return { barBottom: Math.round(bar.bottom), titleTop: Math.round(title.top), titleBottom: Math.round(title.bottom), inner: window.innerHeight,
        pageScroll: document.documentElement.scrollHeight > window.innerHeight + 1, bodyScrolls: body.scrollHeight > body.clientHeight + 1 };
    });
    assert.equal(m.pageScroll, false, `${file}: no page scroll`);
    assert.equal(m.bodyScrolls, true, `${file}: the body region scrolls`);
    assert.ok(m.titleTop >= 0 && m.titleBottom <= m.inner, `${file}: question heading inside the viewport`);
    assert.ok(m.barBottom <= m.inner, `${file}: action bar inside the viewport (${m.barBottom} of ${m.inner})`);
    results.push(`${file} ${width}x${height} bar ${m.barBottom}/${m.inner}`);
    await page.close();
  }
  console.log(`PASS: mobile first, ${results.join("; ")}, heading and actions visible without page scroll, body scrolls`);
}

// The six control run (full context, explicit selection, red gate review, duplicate submit
// lock) stays in its proven file and runs as one scenario here.
export async function controlsAndDuplicateSubmit() {
  const { stdout } = await promisify(execFile)(process.execPath, [resolve(extension, "tests", "browser.integration.js")], { env: process.env, timeout: 180000 });
  const passes = stdout.split("\n").filter((line) => line.startsWith("PASS:"));
  assert.equal(passes.length, 2, `expected two PASS lines from browser.integration.js, got:\n${stdout}`);
  for (const line of passes) console.log(line);
}
