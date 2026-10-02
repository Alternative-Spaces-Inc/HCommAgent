// Host access for the API origin, requested at runtime (HCA-P11).
//
// The API base URL is a deployment value, not repo content, so it cannot
// sit in manifest.json host_permissions without a per-install patch (the
// patch the 2026-10-01 deploy silently reverted). Instead the manifest
// declares optional_host_permissions for https origins and the options
// page asks Chrome for exactly the saved origin when the owner clicks
// Save. Chrome shows one Allow dialog; the grant is stored against the
// extension ID (fixed by the manifest key) and survives unpacked reloads.

// Pure: turns a saved API base URL into the one match pattern to request.
// Throws on anything that is not a plain https origin.
export function originPatternFor(apiBase) {
  if (typeof apiBase !== "string" || !apiBase.trim()) throw new Error("API base URL is empty");
  let url;
  try { url = new URL(apiBase.trim()); } catch (_) { throw new Error("API base URL is not a valid URL"); }
  if (url.protocol !== "https:") throw new Error("API base URL must be https");
  if (url.username || url.password) throw new Error("API base URL must not carry credentials");
  return `${url.protocol}//${url.host}/*`;
}

export async function hasHostAccess(apiBase) {
  const pattern = originPatternFor(apiBase);
  return chrome.permissions.contains({ origins: [pattern] });
}

// Must run from a user gesture (a click handler); Chrome refuses otherwise.
export async function requestHostAccess(apiBase) {
  const pattern = originPatternFor(apiBase);
  if (await chrome.permissions.contains({ origins: [pattern] })) return { pattern, granted: true, prompted: false };
  const granted = await chrome.permissions.request({ origins: [pattern] });
  return { pattern, granted, prompted: true };
}
