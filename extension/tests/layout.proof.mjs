// HCA-P23 Day 1 layout proof (run by hand or by the Day 2 browser job, not by npm test):
// node tests/layout.proof.mjs . renders popup.html at 360x560 and decision.html at 375x667 with a 40 line ask injected
// through a fake chrome.runtime, then measures whether the action bar is inside the viewport
// without page scroll.
import { chromium } from "playwright";
import { resolve, extname } from "node:path";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
const ext = resolve(process.argv[2]);
const lines = Array.from({ length: 40 }, (_, i) => `Line ${i + 1}: the migration slice reported a parity mismatch on the ${i + 1}th file and the harness wants a decision before it continues.`);
const item = { request_id: "r1", title: "Retry the failed slice or roll back the last two slices of work?", why: lines.join(" "), control: "approve_deny",
  options_or_fields: [{ id: "approve", label: "Approve" }, { id: "deny", label: "Deny" }], suggested: { option_id: "approve", why: "The two failures share a timestamp, which reads as flake." },
  expiry_policy: "hold", context: { evidence: lines.slice(0, 10) } };
const snapshot = { ok: true, origin: "https://api.example", deviceId: "dev", cache: { r1: item }, queue: [], notice: "", focusId: "r1" };
const fake = `window.chrome = { runtime: { sendMessage: async (m) => (m.type === "hca_snapshot" ? ${JSON.stringify(snapshot)} : { ok: true }) }, storage: { onChanged: { addListener() {} } } };`;
const types = { ".html": "text/html", ".js": "text/javascript", ".png": "image/png" };
const server = createServer(async (req, res) => {
  const path = resolve(ext, "." + new URL(req.url, "http://x").pathname);
  try { const data = await readFile(path); res.writeHead(200, { "content-type": types[extname(path)] || "application/octet-stream" }); res.end(data); }
  catch { res.writeHead(404); res.end(); }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}/`;
const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });
const results = [];
for (const [file, width, height, q] of [["popup.html", 360, 560, ""], ["decision.html", 375, 667, "?request=r1"]]) {
  const page = await browser.newPage({ viewport: { width, height } });
  await page.addInitScript(fake);
  await page.goto(base + file + q);
  
  await page.waitForSelector(".card-actions button");
  const m = await page.evaluate(() => {
    const bar = document.querySelector(".card-actions").getBoundingClientRect();
    const body = document.querySelector(".card-body");
    const title = document.querySelector("h2").getBoundingClientRect();
    return { barBottom: Math.round(bar.bottom), barTop: Math.round(bar.top), titleTop: Math.round(title.top), inner: window.innerHeight,
      pageScroll: document.documentElement.scrollHeight > window.innerHeight + 1, bodyScrolls: body.scrollHeight > body.clientHeight + 1,
      buttons: [...document.querySelectorAll(".card-actions button")].map((b) => b.textContent.trim()) };
  });
  await page.screenshot({ path: `${process.env.PROOF_OUT || "/tmp"}/p23_${file.replace(".html", "")}_${width}x${height}.png` });
  results.push({ file, width, height, ...m, ok: !m.pageScroll && m.bodyScrolls && m.barBottom <= m.inner && m.titleTop >= 0 });
  await page.close();
}
await browser.close(); server.close();
console.log(JSON.stringify(results, null, 1));
process.exit(results.every((r) => r.ok) ? 0 : 1);
