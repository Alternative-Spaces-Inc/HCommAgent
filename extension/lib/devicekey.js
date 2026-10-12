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

// One in-flight initialization per adapter. Persistent adapters additionally
// implement atomic insert, which protects independent extension contexts.
const initializing = new WeakMap();
export function loadOrCreateDeviceKey(storage, subtle = globalThis.crypto.subtle) {
  if (initializing.has(storage)) return initializing.get(storage);
  const pending = (async () => {
    let stored = await storage.get(STORAGE_KEY);
    if (!stored?.privateKey || !stored?.publicKey) {
      const legacy = stored?.privateJwk ? stored : await storage.legacy?.();
      let pair;
      if (legacy?.privateJwk && legacy?.publicJwk) {
        pair = {
          privateKey: await subtle.importKey("jwk", legacy.privateJwk, P256, false, ["sign"]),
          publicKey: await subtle.importKey("jwk", legacy.publicJwk, P256, true, ["verify"]),
        };
      } else {
        pair = await subtle.generateKey(P256, false, ["sign", "verify"]);
      }
      const candidate = { ...pair, createdAt: legacy?.createdAt || new Date().toISOString() };
      if (storage.insertIfAbsent) stored = await storage.insertIfAbsent(STORAGE_KEY, candidate);
      else { await storage.set(STORAGE_KEY, candidate); stored = candidate; }
    }
    if (stored.privateKey.extractable) throw new Error("Stored device private key must be non-extractable");
    const spki = await subtle.exportKey("spki", stored.publicKey);
    // Delete the old plaintext only after the durable key is readable.
    await storage.clearLegacy?.();
    return { ...stored, deviceId: await deviceIdFromSpki(spki, subtle), publicKeyPem: spkiToPem(spki) };
  })();
  initializing.set(storage, pending);
  pending.catch(() => initializing.delete(storage));
  return pending;
}

// CryptoKeys are structured-cloned by IndexedDB; chrome.storage cannot
// preserve them. A single readwrite transaction selects the winning key.
export function indexedDeviceStorage(indexedDBObj = globalThis.indexedDB, chromeObj = globalThis.chrome) {
  let opening;
  function db() {
    if (!opening) opening = new Promise((resolve, reject) => {
      const req = indexedDBObj.open("hca-device-keys", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("keys");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return opening;
  }
  async function transaction(mode, operation) {
    const handle = await db();
    return new Promise((resolve, reject) => {
      const tx = handle.transaction("keys", mode);
      let value;
      operation(tx.objectStore("keys"), (v) => { value = v; });
      tx.oncomplete = () => resolve(value);
      tx.onerror = tx.onabort = () => reject(tx.error || new Error("Device key storage transaction failed"));
    });
  }
  return {
    get: (key) => transaction("readonly", (store, done) => { const req = store.get(key); req.onsuccess = () => done(req.result); }),
    insertIfAbsent: (key, candidate) => transaction("readwrite", (store, done) => {
      const req = store.get(key);
      req.onsuccess = () => {
        if (req.result) { done(req.result); return; }
        store.put(candidate, key);
        done(candidate);
      };
    }),
    async legacy() { return (await chromeObj.storage.local.get(STORAGE_KEY))[STORAGE_KEY]; },
    async clearLegacy() { await chromeObj.storage.local.remove(STORAGE_KEY); },
    remove: () => transaction("readwrite", (store) => store.delete(STORAGE_KEY)),
  };
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

let deviceStorage;
let cachedPromise;
/** The same durable non-extractable key is shared by every extension context. */
export function currentDevice() {
  if (!cachedPromise) {
    deviceStorage ||= indexedDeviceStorage();
    cachedPromise = loadOrCreateDeviceKey(deviceStorage);
    cachedPromise.catch(() => { cachedPromise = null; });
  }
  return cachedPromise;
}

/** Rotation intentionally requires registering the new public key again. */
export async function rotateDeviceKey() {
  deviceStorage ||= indexedDeviceStorage();
  await deviceStorage.remove();
  await deviceStorage.clearLegacy();
  initializing.delete(deviceStorage);
  cachedPromise = null;
  return currentDevice();
}

/** Refuse redirects so signatures and credentials stay at the chosen host. */
export function createSignedFetch(getDevice, fetchFn) {
  return async (url, init = {}) => {
    const target = new URL(url);
    if (target.protocol !== "https:" || target.username || target.password || target.hash) throw new Error("Signed request requires an HTTPS URL without credentials or fragment");
    const device = await getDevice();
    const method = (init.method || "GET").toUpperCase();
    const body = typeof init.body === "string" ? init.body : "";
    const sigHeaders = await signRequest(device, method, url, body);
    const headers = new Headers(init.headers || {});
    for (const [key, value] of Object.entries(sigHeaders)) headers.set(key, value);
    return fetchFn(url, { ...init, method, headers, redirect: "error", credentials: "omit", cache: "no-store" });
  };
}
export const signedFetch = createSignedFetch(currentDevice, (...args) => fetch(...args));
