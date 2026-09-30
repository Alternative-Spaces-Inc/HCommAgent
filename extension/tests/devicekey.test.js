import { test } from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import {
  b64url, canonicalString, deviceIdFromSpki, loadOrCreateDeviceKey, pathAndQueryOf,
  sha256hex, signRequest, spkiToPem, STORAGE_KEY,
} from "../lib/devicekey.js";

const subtle = webcrypto.subtle;

function memoryStorage() {
  const m = new Map();
  return { async get(k) { return m.get(k); }, async set(k, v) { m.set(k, v); }, map: m };
}

test("sha256hex of empty body matches the server's constant", async () => {
  assert.equal(await sha256hex(new Uint8Array(0), subtle),
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
});

test("canonical string is HCA1, ts, nonce, METHOD, path?query, body hash, newline joined", () => {
  const s = canonicalString(1700000000, "n0nce", "post", "/v1/requests/abc/answer", "00ff");
  assert.equal(s, "HCA1\n1700000000\nn0nce\nPOST\n/v1/requests/abc/answer\n00ff");
});

test("pathAndQueryOf keeps the query and drops the origin", () => {
  assert.equal(pathAndQueryOf("https://x.example/v1/audit?agent=one&outcome=deny"), "/v1/audit?agent=one&outcome=deny");
  assert.equal(pathAndQueryOf("https://x.example/v1/inbox"), "/v1/inbox");
});

test("b64url has no padding and uses - and _", () => {
  assert.equal(b64url(new Uint8Array([251, 255, 254])), "-__-");
  assert.equal(b64url(new Uint8Array([1])), "AQ");
});

test("loadOrCreateDeviceKey generates once, persists, and derives a stable dev_ id", async () => {
  const storage = memoryStorage();
  const a = await loadOrCreateDeviceKey(storage, subtle);
  assert.match(a.deviceId, /^dev_[0-9a-f]{32}$/);
  assert.ok(storage.map.has(STORAGE_KEY));
  assert.match(a.publicKeyPem, /^-----BEGIN PUBLIC KEY-----\n[\s\S]+\n-----END PUBLIC KEY-----\n$/);
  const b = await loadOrCreateDeviceKey(storage, subtle);
  assert.equal(b.deviceId, a.deviceId);
  assert.equal(b.publicKeyPem, a.publicKeyPem);
  const spki = await subtle.exportKey("spki", a.publicKey);
  assert.equal(await deviceIdFromSpki(spki, subtle), a.deviceId);
  assert.equal(spkiToPem(spki), a.publicKeyPem);
});

test("signRequest produces a raw 64-byte signature the public key verifies over the canonical string", async () => {
  const storage = memoryStorage();
  const device = await loadOrCreateDeviceKey(storage, subtle);
  const body = JSON.stringify({ control: "radio", value: "a", surface: "browser_overlay" });
  const headers = await signRequest(device, "POST", "https://x.example/v1/requests/r1/answer", body,
    { subtle, crypto: webcrypto, timestamp: 1700000000, nonce: "fixed" });
  assert.equal(headers["X-HCA-Device"], device.deviceId);
  assert.equal(headers["X-HCA-Timestamp"], "1700000000");
  assert.equal(headers["X-HCA-Nonce"], "fixed");
  const sig = headers["X-HCA-Signature"];
  assert.doesNotMatch(sig, /[+/=]/);
  const raw = Uint8Array.from(atob(sig.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - sig.length % 4) % 4)), (c) => c.charCodeAt(0));
  assert.equal(raw.length, 64);
  const bodyHash = await sha256hex(new TextEncoder().encode(body), subtle);
  const canonical = canonicalString(1700000000, "fixed", "POST", "/v1/requests/r1/answer", bodyHash);
  const ok = await subtle.verify({ name: "ECDSA", hash: { name: "SHA-256" } }, device.publicKey, raw, new TextEncoder().encode(canonical));
  assert.equal(ok, true);
  const bad = await subtle.verify({ name: "ECDSA", hash: { name: "SHA-256" } }, device.publicKey, raw,
    new TextEncoder().encode(canonical.replace("/answer", "/cancel")));
  assert.equal(bad, false);
});
