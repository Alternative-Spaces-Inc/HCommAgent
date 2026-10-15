// Runner for the P23 browser proofs: every scenario prints one PASS line; any failure
// fails the process. CI runs this through npm run test:browser (gates.yml, extension job).
const { chromium } = await import(process.env.HCA_PLAYWRIGHT_MODULE || "playwright");
import { serveExtension } from "./harness.js";
const scenarios = [];
for (const file of ["./offline_queue.js", "./stream_and_notify.js", "./pages.js"]) scenarios.push(...Object.entries(await import(file)));
const server = await serveExtension();
let browser; let failed = 0;
try {
  browser = await chromium.launch({ headless: true, executablePath: process.env.HCA_CHROMIUM_PATH || undefined });
  for (const [name, run] of scenarios) {
    const context = await browser.newContext();
    const started = Date.now();
    try { await run(context, server.base); console.log(`      ${name} ${Date.now() - started} ms`); }
    catch (error) { failed += 1; console.log(`FAIL: ${name}: ${error.stack || error}`); }
    finally { await context.close(); }
  }
} finally { await browser?.close(); await server.close(); }
if (failed) { console.log(`${failed} browser proof(s) failed`); process.exit(1); }
