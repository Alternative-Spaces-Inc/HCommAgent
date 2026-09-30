import { currentDevice } from "./lib/devicekey.js";

const API_BASE_KEY = "hca_api_base";

async function load() {
  const stored = await chrome.storage.local.get(API_BASE_KEY);
  document.getElementById("api-base").value = stored[API_BASE_KEY] || "";
  await showDevice();
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
load();
