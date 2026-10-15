// Packaging selects a coherent public key / OAuth pairing for each target.
// Raw extension/ stays unpacked-safe; the default artifact is store-safe.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const clients = JSON.parse(readFileSync(join(here, "..", "oauth-clients.json"), "utf8"));
const manifest = JSON.parse(readFileSync(join(here, "..", "manifest.json"), "utf8"));

function pack(commit, extra = []) {
  const out = mkdtempSync(join(tmpdir(), "hca-pack-"));
  const stdout = execFileSync("python3", [join(repo, "scripts", "pack_extension.py"), "--out", out, "--commit", commit, ...extra], { encoding: "utf8" });
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

test("default store pack drops the key, selects the store OAuth client and reports identity", () => {
  assert.ok(manifest.key, "repo manifest keeps the key for unpacked loads");
  const r = pack("abcdef0123456789");
  assert.equal(r.version, manifest.version);
  assert.equal(r.version_name, `${manifest.version}+abcdef0`);
  assert.equal(r.key_present, false);
  assert.match(r.sha256, /^[0-9a-f]{64}$/);
  const m = zipManifest(r.zip);
  assert.equal(m.key, undefined);
  assert.equal(m.version, manifest.version);
  assert.equal(m.version_name, `${manifest.version}+abcdef0`);
  assert.deepEqual(m.permissions, manifest.permissions);
  assert.equal(r.target, "store");
  assert.equal(r.extension_id, clients.store.extension_id);
  assert.equal(r.oauth_client_id, clients.store.client_id);
  assert.deepEqual(m.oauth2, { ...manifest.oauth2, client_id: clients.store.client_id });
});

test("pack --keep-key keeps the repo key for an unpacked load", () => {
  const r = pack("abcdef0123456789", ["--keep-key"]);
  assert.equal(r.key_present, true);
  assert.equal(zipManifest(r.zip).key, manifest.key);
  assert.equal(r.target, "unpacked");
  assert.equal(r.oauth_client_id, clients.unpacked.client_id);
  assert.equal(zipManifest(r.zip).oauth2.client_id, clients.unpacked.client_id);
  assert.ok(r.zip.endsWith("-unpacked.zip"));
});

test("pack leaves tests and package files out and ships the runtime files", () => {
  const r = pack("");
  const names = zipNames(r.zip);
  for (const required of ["manifest.json", "background.js", "popup.html", "popup.js", "options.html", "options.js", "lib/api.js", "lib/devicekey.js", "lib/hostaccess.js", "lib/offlinequeue.js", "icons/icon16.png", "icons/icon48.png", "icons/icon128.png"]) {
    assert.ok(names.includes(required), `missing ${required}`);
  }
  assert.ok(!names.some((n) => n.startsWith("tests/") || n === "package.json" || n === "package-lock.json" || n === "oauth-clients.json"));
  assert.ok(r.zip.endsWith(`extension-${manifest.version}-local.zip`));
});

test("manifest version is a dotted number the store accepts as a release counter", () => {
  assert.match(manifest.version, /^\d+(\.\d+){1,3}$/);
});

// HCA-P23 Day 1: the manifest's security surface is pinned. A widened permission list, a
// non empty host_permissions, a looser optional pattern or a weaker CSP fails here before
// it reaches a store upload or an unpacked load.
test("manifest permissions, host access and CSP are exactly the pinned set", () => {
  assert.deepEqual(manifest.permissions, ["storage", "notifications", "alarms", "identity"]);
  assert.deepEqual(manifest.host_permissions, []);
  assert.deepEqual(manifest.optional_host_permissions, ["https://*/*"]);
  assert.deepEqual(manifest.content_security_policy, { extension_pages: "script-src 'self'; object-src 'self'; connect-src https:" });
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.version, "0.4.0");
  const pkg = JSON.parse(readFileSync(join(here, "..", "package.json"), "utf8"));
  assert.equal(pkg.version, manifest.version);
});

test("the packed zip carries the CSP and the decision page", () => {
  const r = pack("abcdef0123456789");
  assert.deepEqual(zipManifest(r.zip).content_security_policy, manifest.content_security_policy);
  const names = zipNames(r.zip);
  assert.ok(names.includes("decision.html"), `decision.html missing from ${names.join(", ")}`);
  assert.ok(names.includes("popup.html"));
});


test("raw folder pairs the existing unpacked key and client without manual edits", () => {
  const hash = createHash("sha256").update(Buffer.from(manifest.key, "base64")).digest("hex").slice(0, 32);
  const id = [...hash].map(n => String.fromCharCode(97 + parseInt(n, 16))).join("");
  assert.equal(id, "jmpjjfhgggjmbebejaapchodjacmbmpm");
  assert.equal(clients.unpacked.extension_id, id);
  assert.equal(manifest.oauth2.client_id, clients.unpacked.client_id);
  assert.equal(clients.unpacked.client_id, "529822100897-kqt82297rvh559desb96e4ehp06fttka.apps.googleusercontent.com");
  assert.equal(clients.store.extension_id, "icaocojldacopllkkjdhghkeoeghccfe");
  assert.equal(clients.store.client_id, "529822100897-taeem2o8rr3av3s1v1tv7oj9eaf3efrr.apps.googleusercontent.com");
});

test("explicit targets retain stable store output and give unpacked output a distinct name", () => {
  const before = readFileSync(join(here, "..", "manifest.json"), "utf8");
  const implicit = pack("abcdef0123456789");
  const store = pack("abcdef0123456789", ["--target", "store"]);
  const unpacked = pack("abcdef0123456789", ["--target", "unpacked"]);
  assert.equal(implicit.sha256, store.sha256);
  assert.notEqual(store.sha256, unpacked.sha256);
  assert.equal(unpacked.extension_id, clients.unpacked.extension_id);
  assert.equal(zipManifest(unpacked.zip).key, manifest.key);
  assert.deepEqual(zipManifest(unpacked.zip).oauth2, manifest.oauth2);
  assert.notEqual(store.zip.split("/").pop(), unpacked.zip.split("/").pop());
  assert.equal(readFileSync(join(here, "..", "manifest.json"), "utf8"), before);
  assert.ok(!zipNames(store.zip).includes("oauth-clients.json"));
  assert.ok(!zipNames(unpacked.zip).includes("oauth-clients.json"));
});

function refuses(label, mutate, extra = [], expected = /pack:/) {
  test(label, () => {
    const fixture = mkdtempSync(join(tmpdir(), "hca-pairing-"));
    try {
      mkdirSync(join(fixture, "scripts"));
      cpSync(join(repo, "scripts", "pack_extension.py"), join(fixture, "scripts", "pack_extension.py"));
      cpSync(join(repo, "extension"), join(fixture, "extension"), {
        recursive: true, filter: path => !path.split(/[\\/]/).includes("node_modules")
      });
      const m = structuredClone(manifest), c = structuredClone(clients);
      mutate(m, c);
      writeFileSync(join(fixture, "extension", "manifest.json"), JSON.stringify(m));
      writeFileSync(join(fixture, "extension", "oauth-clients.json"), JSON.stringify(c));
      const out = join(fixture, "out");
      const r = spawnSync("python3", [join(fixture, "scripts", "pack_extension.py"), "--out", out, ...extra], { encoding: "utf8" });
      assert.notEqual(r.status, 0);
      assert.match(r.stderr, expected);
      assert.equal(existsSync(out), false, "invalid pairing must fail before writing output");
    } finally { rmSync(fixture, { recursive: true, force: true }); }
  });
}

refuses("rejects store client in the source manifest", (m, c) => { m.oauth2.client_id = c.store.client_id; }, [], /does not match unpacked mapping/);
refuses("rejects missing source public key", m => { delete m.key; }, [], /retain its public unpacked key/);
refuses("rejects malformed public key", m => { m.key = "%%%"; }, [], /not valid base64/);
refuses("rejects changed public key identity", m => { m.key = Buffer.from("different public key").toString("base64"); }, [], /does not match unpacked extension ID/);
refuses("rejects malformed OAuth client mapping", (m, c) => { c.store.client_id = "invalid"; }, [], /invalid client ID/);
refuses("rejects malformed extension identity", (m, c) => { c.store.extension_id = "invalid"; }, [], /invalid extension ID/);
refuses("rejects shared clients across targets", (m, c) => { c.store.client_id = c.unpacked.client_id; }, [], /must be distinct/);
refuses("rejects shared IDs across targets", (m, c) => { c.store.extension_id = c.unpacked.extension_id; }, [], /must be distinct/);
refuses("rejects missing target mapping", (m, c) => { delete c.store; }, [], /exactly unpacked and store/);
refuses("rejects unexpected mapping target", (m, c) => { c.other = c.store; }, [], /exactly unpacked and store/);
refuses("rejects missing OAuth metadata in source", m => { delete m.oauth2; }, [], /does not match unpacked mapping/);
refuses("rejects incomplete client mapping", (m, c) => { delete c.store.client_id; }, [], /needs extension_id and client_id/);
refuses("rejects --keep-key with explicit store target", () => {}, ["--target", "store", "--keep-key"], /conflicts/);
refuses("rejects unknown target", () => {}, ["--target", "other"], /invalid choice/);
