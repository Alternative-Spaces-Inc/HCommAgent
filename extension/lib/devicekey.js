// Per-device ECDSA P-256 request signing for the human surface routes
// (HCA-P6). Mirrors api/app/devicesig.py exactly; the server's canonical
// string is
//   "HCA1\n" + timestamp + "\n" + nonce + "\n" + METHOD + "\n" + pathAndQuery + "\n" + sha256hex(body)
// and the signature header carries base64url (no padding) of the raw
// 64-byte r||s signature WebCrypto produces.
//
// Framework free and Node-testable: the WebCrypto and storage handles are
// injected so tests run without chrome.* (Node 20 has globalThis.crypto).

export const STORAGE_KEY = "hca_device_key";
export const PREFIX = "HCA1";

const P256 = { name: "ECDSA", namedCurve: "P-256" };
const SIGN_ALG = { name: "ECDSA", hash: { name: "SHA-256" } };

export function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function toHex(bytes) {
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256hex(bytes, subtle = globalThis.crypto.subtle) {
  return toHex(await subtle.digest("SHA-256", bytes));
}

/** dev_ + first 32 hex chars of SHA-256 over the SubjectPublicKeyInfo DER. */
export async function deviceIdFromSpki(spkiDer, subtle = globalThis.crypto.subtle) {
  return "dev_" + (await sha256hex(spkiDer, subtle)).slice(0, 32);
}

export function spkiToPem(spkiDer) {
  const b64 = btoa(String.fromCharCode(...new Uint8Array(spkiDer)));
  const lines = b64.match(/.{1,64}/g).join("\n");
  return `-----BEGIN PUBLIC KEY-----\n${lines}\n-----END PUBLIC KEY-----\n`;
}

export function canonicalString(timestamp, nonce, method, pathAndQuery, bodyHashHex) {
  return [PREFIX, String(timestamp), nonce, method.toUpperCase(), pathAndQuery, bodyHashHex].join("\n");
}

/** Path plus "?query" of an absolute URL, exactly what the server sees. */
export function pathAndQueryOf(url) {
  const u = new URL(url);
  return u.pathname + (u.search || "");
}

export function randomNonce(cryptoObj = globalThis.crypto) {
  const bytes = new Uint8Array(12);
  cryptoObj.getRandomValues(bytes);
  return b64url(bytes);
}

export const CLAIM_TTL_MS = 10000;
const CLAIM_POLL_MS = 50;

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function hasKey(stored) {
  return !!(stored && stored.privateJwk && stored.publicJwk);
}

async function importStored(stored, subtle) {
  const privateKey = await subtle.importKey("jwk", stored.privateJwk, P256, false, ["sign"]);
  const publicKey = await subtle.importKey("jwk", stored.publicJwk, P256, true, ["verify"]);
  const spki = await subtle.exportKey("spki", publicKey);
  return {
    privateKey,
    publicKey,
    deviceId: await deviceIdFromSpki(spki, subtle),
    publicKeyPem: spkiToPem(spki),
    createdAt: stored.createdAt,
  };
}

/**
 * Load the device key from storage or generate and store a new one.
 * storage is an object with async get(key) -> value and set(key, value).
 * Returns { privateKey, publicKey, deviceId, publicKeyPem }.
 *
 * Single flight across extension contexts (review item 16): the background
 * service worker, the popup and the options page share chrome.storage.local
 * but not memory, and all three may start at once. Without coordination
 * each would generate its own pair and the last write would win, leaving
 * two contexts signing with a key the server has never seen. The protocol
 * is claim, then verify, then write:
 *   1. read; a stored key wins immediately.
 *   2. write a claim {claim: <random>, claimedAt} (no key yet). set() is
 *      last writer wins, so after a short settle every claimant re-reads.
 *   3. if the stored claim is ours, generate and write the key; otherwise
 *      wait for the winner's key to appear (poll, bounded by CLAIM_TTL_MS).
 *   4. a claim older than CLAIM_TTL_MS with no key is treated as abandoned
 *      (its context died between claim and write) and may be re-claimed.
 * Custody: the private key is stored as a JWK in chrome.storage.local, which
 * is readable by this extension only, but by any code that can read the
 * profile directory on disk. That is the local compromise assumption; the
 * server limits the blast radius to this one device id, which the owner can
 * revoke and re-register (rotateDeviceKey below starts that from the client).
 */
export async function loadOrCreateDeviceKey(storage, subtle = globalThis.crypto.subtle, opts = {}) {
  const now = opts.now || (() => Date.now());
  const settleMs = opts.settleMs !== undefined ? opts.settleMs : CLAIM_POLL_MS;
  const cryptoObj = opts.crypto || globalThis.crypto;
  const deadline = now() + CLAIM_TTL_MS;
  for (;;) {
    let stored = await storage.get(STORAGE_KEY);
    if (hasKey(stored)) return importStored(stored, subtle);
    const claimFresh = stored && stored.claim && typeof stored.claimedAt === "number" && now() - stored.claimedAt < CLAIM_TTL_MS;
    if (!claimFresh) {
      const mine = { claim: randomNonce(cryptoObj), claimedAt: now() };
      await storage.set(STORAGE_KEY, mine);
      await sleep(settleMs);
      stored = await storage.get(STORAGE_KEY);
      if (hasKey(stored)) return importStored(stored, subtle);
      if (stored && stored.claim === mine.claim) {
        const pair = await subtle.generateKey(P256, true, ["sign", "verify"]);
        const record = {
          privateJwk: await subtle.exportKey("jwk", pair.privateKey),
          publicJwk: await subtle.exportKey("jwk", pair.publicKey),
          createdAt: new Date(now()).toISOString(),
        };
        // Verify the claim survived the generation window before the write;
        // a competing fresh claimant would otherwise be overwritten.
        const check = await storage.get(STORAGE_KEY);
        if (hasKey(check)) return importStored(check, subtle);
        if (check && check.claim !== mine.claim) continue;
        await storage.set(STORAGE_KEY, record);
        return importStored(record, subtle);
      }
    }
    // Someone else holds a fresh claim: wait for their key.
    if (now() > deadline) throw new Error("device key initialization timed out waiting for another context");
    await sleep(settleMs);
  }
}

/**
 * Replace the device key (review item 16, rotation). The old key is kept
 * under previous so the options page can show the retiring device id; the
 * new public key must be registered with the server before any signed
 * call succeeds again. Returns the new device.
 */
export async function rotateDeviceKey(storage, subtle = globalThis.crypto.subtle) {
  const old = await storage.get(STORAGE_KEY);
  const pair = await subtle.generateKey(P256, true, ["sign", "verify"]);
  const record = {
    privateJwk: await subtle.exportKey("jwk", pair.privateKey),
    publicJwk: await subtle.exportKey("jwk", pair.publicKey),
    createdAt: new Date().toISOString(),
    previous: hasKey(old) ? { publicJwk: old.publicJwk, createdAt: old.createdAt, retiredAt: new Date().toISOString() } : undefined,
  };
  await storage.set(STORAGE_KEY, record);
  _cached = null;
  _inflight = null;
  return importStored(record, subtle);
}

/**
 * Sign one request. body is a string (already serialized) or empty.
 * Returns the four X-HCA-* headers.
 */
export async function signRequest(device, method, url, body = "", opts = {}) {
  const subtle = opts.subtle || globalThis.crypto.subtle;
  const cryptoObj = opts.crypto || globalThis.crypto;
  const timestamp = opts.timestamp !== undefined ? opts.timestamp : Math.floor(Date.now() / 1000);
  const nonce = opts.nonce || randomNonce(cryptoObj);
  const enc = new TextEncoder();
  const bodyHash = await sha256hex(enc.encode(body || ""), subtle);
  const canonical = canonicalString(timestamp, nonce, method, pathAndQueryOf(url), bodyHash);
  const sig = await subtle.sign(SIGN_ALG, device.privateKey, enc.encode(canonical));
  return {
    "X-HCA-Device": device.deviceId,
    "X-HCA-Timestamp": String(timestamp),
    "X-HCA-Nonce": nonce,
    "X-HCA-Signature": b64url(sig),
  };
}

/** chrome.storage.local adapter for loadOrCreateDeviceKey. */
export function chromeLocalStorage(chromeObj = globalThis.chrome) {
  return {
    async get(key) {
      const r = await chromeObj.storage.local.get(key);
      return r[key];
    },
    async set(key, value) {
      await chromeObj.storage.local.set({ [key]: value });
    },
  };
}

let _cached = null;
let _inflight = null;
/**
 * The extension's own device, loaded once per service worker or page life.
 * Concurrent callers inside one context share a single in flight promise,
 * so two early signedFetch calls cannot both run the create path.
 */
export async function currentDevice(storage = null) {
  if (_cached) return _cached;
  if (!_inflight) {
    _inflight = loadOrCreateDeviceKey(storage || chromeLocalStorage())
      .then((d) => { _cached = d; return d; })
      .finally(() => { _inflight = null; });
  }
  return _inflight;
}

/** Test and rotation hook: forget the in memory device. */
export function resetDeviceCache() {
  _cached = null;
  _inflight = null;
}

/** fetch with the device signature headers added. Body must be a string. */
export async function signedFetch(url, init = {}) {
  const device = await currentDevice();
  const method = (init.method || "GET").toUpperCase();
  const body = typeof init.body === "string" ? init.body : "";
  const sigHeaders = await signRequest(device, method, url, body);
  const headers = Object.assign({}, init.headers || {}, sigHeaders);
  return fetch(url, Object.assign({}, init, { method, headers }));
}
