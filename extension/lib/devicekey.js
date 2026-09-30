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

/**
 * Load the device key from storage or generate and store a new one.
 * storage is an object with async get(key) -> value and set(key, value).
 * Returns { privateKey, publicKey, deviceId, publicKeyPem }.
 */
export async function loadOrCreateDeviceKey(storage, subtle = globalThis.crypto.subtle) {
  let stored = await storage.get(STORAGE_KEY);
  if (!stored || !stored.privateJwk || !stored.publicJwk) {
    const pair = await subtle.generateKey(P256, true, ["sign", "verify"]);
    stored = {
      privateJwk: await subtle.exportKey("jwk", pair.privateKey),
      publicJwk: await subtle.exportKey("jwk", pair.publicKey),
      createdAt: new Date().toISOString(),
    };
    await storage.set(STORAGE_KEY, stored);
  }
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
/** The extension's own device, loaded once per service worker or page life. */
export async function currentDevice() {
  if (!_cached) _cached = await loadOrCreateDeviceKey(chromeLocalStorage());
  return _cached;
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
