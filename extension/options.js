import { currentDevice } from "./lib/devicekey.js";
import { fetchSelfDevice, linkGoogle } from "./lib/api.js";

const API_BASE_KEY = "hca_api_base";

async function load() {
  const stored = await chrome.storage.local.get(API_BASE_KEY);
  document.getElementById("api-base").value = stored[API_BASE_KEY] || "";
  await showDevice();
  await showGoogle();
}

async function apiBase() {
  const stored = await chrome.storage.local.get(API_BASE_KEY);
  return stored[API_BASE_KEY] || "";
}

function setGoogleStatus(text, isError = false) {
  const el = document.getElementById("google-status");
  el.textContent = text;
  el.className = isError ? "error" : "note";
}

// Reads the linked account from the server (GET /v1/devices/self, device
// signed). A 403 here means the device is not registered yet.
async function showGoogle() {
  const base = await apiBase();
  if (!base) { setGoogleStatus("Save the API base URL first."); return; }
  try {
    const view = await fetchSelfDevice(base);
    setGoogleStatus(view.google_email
      ? `Linked to ${view.google_email} since ${view.google_linked_at}`
      : "Not linked to a Google account yet.");
  } catch (e) {
    setGoogleStatus(`Could not read this device from the server (${e.message}). Is it registered?`, true);
  }
}

// chrome.identity.getAuthToken uses the oauth2 block in manifest.json
// (the Chrome Extension OAuth client bound to this extension ID). The
// access token goes to the server over a device-signed call; the server
// checks it against the client id and the owner list (app/googleauth.py).
async function signInWithGoogle() {
  const base = await apiBase();
  if (!base) { setGoogleStatus("Save the API base URL first.", true); return; }
  setGoogleStatus("Signing in...");
  let token;
  try {
    const got = await chrome.identity.getAuthToken({ interactive: true });
    token = typeof got === "string" ? got : got && got.token;
  } catch (e) {
    setGoogleStatus(`Google sign-in failed: ${e.message}`, true);
    return;
  }
  if (!token) { setGoogleStatus("Google sign-in returned no token.", true); return; }
  try {
    const view = await linkGoogle(base, token);
    setGoogleStatus(`Linked to ${view.google_email} since ${view.google_linked_at}`);
  } catch (e) {
    // A refused token stays in Chrome's cache; drop it so the next try asks again.
    try { await chrome.identity.removeCachedAuthToken({ token }); } catch (_) { /* best effort */ }
    setGoogleStatus(`Server refused the link: ${e.message}`, true);
  }
}

async function showDevice() {
  const device = await currentDevice();
  document.getElementById("device-id").textContent = device.deviceId;
  document.getElementById("device-pem").value = device.publicKeyPem;
  document.getElementById("device-created").textContent = device.createdAt || "";
}

async function save() {
  const value = document.getElementById("api-base").value.trim().replace(/\/+$/, "");
  await chrome.storage.local.set({ [API_BASE_KEY]: value });
  const saved = document.getElementById("saved");
  saved.hidden = false;
  setTimeout(() => { saved.hidden = true; }, 1500);
}

async function copyPem() {
  const pem = document.getElementById("device-pem").value;
  await navigator.clipboard.writeText(pem);
  const copied = document.getElementById("copied");
  copied.hidden = false;
  setTimeout(() => { copied.hidden = true; }, 1500);
}

document.getElementById("save").addEventListener("click", save);
document.getElementById("copy-pem").addEventListener("click", copyPem);
document.getElementById("google-signin").addEventListener("click", signInWithGoogle);
load();
