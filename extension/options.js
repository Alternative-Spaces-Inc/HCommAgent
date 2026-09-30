const API_BASE_KEY = "hca_api_base";

async function load() {
  const stored = await chrome.storage.local.get(API_BASE_KEY);
  document.getElementById("api-base").value = stored[API_BASE_KEY] || "";
}

async function save() {
  const value = document.getElementById("api-base").value.trim().replace(/\/+$/, "");
  await chrome.storage.local.set({ [API_BASE_KEY]: value });
  const saved = document.getElementById("saved");
  saved.hidden = false;
  setTimeout(() => { saved.hidden = true; }, 1500);
}

document.getElementById("save").addEventListener("click", save);
load();
