// Review item 16: device key creation is single flight across contexts and
// inside one context, survives a service worker restart, and can be rotated.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  loadOrCreateDeviceKey, currentDevice, resetDeviceCache, rotateDeviceKey, STORAGE_KEY,
} from "../lib/devicekey.js";

// A shared storage with asynchronous, interleaving reads and writes: what
// chrome.storage.local looks like to three contexts starting at once.
function sharedStorage(delayMs = 2) {
  const data = new Map();
  const log = [];
  const tick = () => new Promise((r) => setTimeout(r, delayMs));
  return {
    data, log,
    async get(key) { await tick(); log.push(["get", key]); return data.get(key); },
    async set(key, value) { await tick(); log.push(["set", key, value.claim ? "claim" : value.privateJwk ? "key" : "other"]); data.set(key, value); },
  };
}

function countingSubtle() {
  const real = globalThis.crypto.subtle;
  const counts = { generateKey: 0 };
  return {
    counts,
    async generateKey(...a) { counts.generateKey += 1; return real.generateKey(...a); },
    exportKey: (...a) => real.exportKey(...a),
    importKey: (...a) => real.importKey(...a),
    digest: (...a) => real.digest(...a),
    sign: (...a) => real.sign(...a),
    verify: (...a) => real.verify(...a),
  };
}

test("three contexts starting simultaneously end with one shared key", async () => {
  const storage = sharedStorage();
  const subtle = countingSubtle();
  const devices = await Promise.all([
    loadOrCreateDeviceKey(storage, subtle, { settleMs: 5 }),
    loadOrCreateDeviceKey(storage, subtle, { settleMs: 5 }),
    loadOrCreateDeviceKey(storage, subtle, { settleMs: 5 }),
  ]);
  const ids = new Set(devices.map((d) => d.deviceId));
  assert.equal(ids.size, 1, `expected one device id, got ${[...ids].join(", ")}`);
  assert.equal(subtle.counts.generateKey, 1, "exactly one key pair generated");
  const stored = storage.data.get(STORAGE_KEY);
  assert.ok(stored.privateJwk && stored.publicJwk);
  assert.equal(storage.log.filter((e) => e[0] === "set" && e[2] === "key").length, 1);
});

test("a later context (service worker restart) loads the same key without generating", async () => {
  const storage = sharedStorage();
  const subtle = countingSubtle();
  const first = await loadOrCreateDeviceKey(storage, subtle, { settleMs: 1 });
  const again = await loadOrCreateDeviceKey(storage, subtle, { settleMs: 1 });
  assert.equal(again.deviceId, first.deviceId);
  assert.equal(subtle.counts.generateKey, 1);
});

test("an abandoned claim older than the TTL is re-claimed", async () => {
  const storage = sharedStorage();
  storage.data.set(STORAGE_KEY, { claim: "dead-context", claimedAt: 0 });
  let t = 1_000_000;
  const dev = await loadOrCreateDeviceKey(storage, globalThis.crypto.subtle, { settleMs: 1, now: () => (t += 1) });
  assert.match(dev.deviceId, /^dev_[0-9a-f]{32}$/);
});

test("a fresh claim held by another context is waited on, not overwritten", async () => {
  const storage = sharedStorage();
  const subtle = countingSubtle();
  storage.data.set(STORAGE_KEY, { claim: "other-context", claimedAt: Date.now() });
  // The other context finishes after 30 ms.
  setTimeout(async () => {
    const pair = await globalThis.crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
    storage.data.set(STORAGE_KEY, {
      privateJwk: await globalThis.crypto.subtle.exportKey("jwk", pair.privateKey),
      publicJwk: await globalThis.crypto.subtle.exportKey("jwk", pair.publicKey),
      createdAt: "x",
    });
  }, 30);
  const dev = await loadOrCreateDeviceKey(storage, subtle, { settleMs: 5 });
  assert.equal(subtle.counts.generateKey, 0, "the waiter never generated");
  assert.match(dev.deviceId, /^dev_/);
});

test("currentDevice shares one in flight promise inside a context", async () => {
  resetDeviceCache();
  const storage = sharedStorage();
  const [a, b, c] = await Promise.all([currentDevice(storage), currentDevice(storage), currentDevice(storage)]);
  assert.equal(a, b);
  assert.equal(b, c);
  assert.equal(storage.log.filter((e) => e[0] === "set" && e[2] === "key").length, 1);
  resetDeviceCache();
});

test("rotateDeviceKey yields a new id and keeps the retired public key", async () => {
  const storage = sharedStorage();
  const first = await loadOrCreateDeviceKey(storage, globalThis.crypto.subtle, { settleMs: 1 });
  const rotated = await rotateDeviceKey(storage);
  assert.notEqual(rotated.deviceId, first.deviceId);
  const stored = storage.data.get(STORAGE_KEY);
  assert.ok(stored.previous && stored.previous.publicJwk);
  assert.ok(stored.previous.retiredAt);
  const reloaded = await loadOrCreateDeviceKey(storage, globalThis.crypto.subtle, { settleMs: 1 });
  assert.equal(reloaded.deviceId, rotated.deviceId);
});
