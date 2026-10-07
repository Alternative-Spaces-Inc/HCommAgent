// HCA-P17: the Web Store zip built by scripts/pack_extension.py keeps the
// manifest key (the extension ID depends on it), bumps nothing but
// version_name, and leaves tests and package files out.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const manifest = JSON.parse(readFileSync(join(here, "..", "manifest.json"), "utf8"));

function pack(commit) {
  const out = mkdtempSync(join(tmpdir(), "hca-pack-"));
  const stdout = execFileSync("python3", [join(repo, "scripts", "pack_extension.py"), "--out", out, "--commit", commit], { encoding: "utf8" });
  return JSON.parse(stdout.trim().split("\n").pop());
}

function zipNames(zipPath) {
  const stdout = execFileSync("python3", ["-c", "import sys,zipfile,json; print(json.dumps(sorted(zipfile.ZipFile(sys.argv[1]).namelist())))", zipPath], { encoding: "utf8" });
  return JSON.parse(stdout);
}

function zipManifest(zipPath) {
  const stdout = execFileSync("python3", ["-c", "import sys,zipfile; sys.stdout.write(zipfile.ZipFile(sys.argv[1]).read('manifest.json').decode())", zipPath], { encoding: "utf8" });
  return JSON.parse(stdout);
}

test("pack keeps the key, sets version_name, reports the version", () => {
  const r = pack("abcdef0123456789");
  assert.equal(r.version, manifest.version);
  assert.equal(r.version_name, `${manifest.version}+abcdef0`);
  assert.equal(r.key_present, true);
  assert.match(r.sha256, /^[0-9a-f]{64}$/);
  const m = zipManifest(r.zip);
  assert.equal(m.key, manifest.key);
  assert.equal(m.version, manifest.version);
  assert.equal(m.version_name, `${manifest.version}+abcdef0`);
  assert.deepEqual(m.permissions, manifest.permissions);
  assert.deepEqual(m.oauth2, manifest.oauth2);
});

test("pack leaves tests and package files out and ships the runtime files", () => {
  const r = pack("");
  const names = zipNames(r.zip);
  for (const required of ["manifest.json", "background.js", "popup.html", "popup.js", "options.html", "options.js", "lib/api.js", "lib/devicekey.js", "lib/hostaccess.js", "lib/offlinequeue.js", "icons/icon16.png", "icons/icon48.png", "icons/icon128.png"]) {
    assert.ok(names.includes(required), `missing ${required}`);
  }
  assert.ok(!names.some((n) => n.startsWith("tests/") || n === "package.json" || n === "package-lock.json"));
  assert.ok(r.zip.endsWith(`extension-${manifest.version}-local.zip`));
});

test("manifest version is a dotted number the store accepts as a release counter", () => {
  assert.match(manifest.version, /^\d+(\.\d+){1,3}$/);
});
