// Only a plain HTTPS origin is a trusted endpoint. Route/query/fragment
// strings must never silently redirect signed requests to a different API.
export function normalizeApiOrigin(apiBase) {
  if (typeof apiBase !== "string" || !apiBase.trim()) throw new Error("API base URL is empty");
  let url;
  try { url = new URL(apiBase.trim()); } catch (_) { throw new Error("API base URL is not a valid URL"); }
  if (url.protocol !== "https:") throw new Error("API base URL must be https");
  if (url.username || url.password) throw new Error("API base URL must not carry credentials");
  if (url.pathname !== "/" || url.search || url.hash) throw new Error("API base URL must be an origin without a path, query or fragment");
  return url.origin;
}

export function originPatternFor(apiBase) { return `${normalizeApiOrigin(apiBase)}/*`; }
export async function hasHostAccess(apiBase) {
  return chrome.permissions.contains({ origins: [originPatternFor(apiBase)] });
}
// Chrome requires the permission request inside a user gesture.
export async function requestHostAccess(apiBase) {
  const pattern = originPatternFor(apiBase);
  if (await chrome.permissions.contains({ origins: [pattern] })) return { pattern, granted: true, prompted: false };
  return { pattern, granted: await chrome.permissions.request({ origins: [pattern] }), prompted: true };
}
