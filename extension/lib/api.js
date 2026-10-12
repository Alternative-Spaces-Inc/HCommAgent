// Shared, framework-free helpers for the Human Collections Agent browser
// overlay client (background service worker and popup both import this).
//
// The endpoints and payload shapes here follow api/openapi.yaml, not the
// stale draft field names in docs/plans/HCA_CLOUD_RUN_PLAN_v1.md section 3
// (that table predates the 2026-09-29 reconciliation; see the
// reconciliation table at the top of that same plan document for the list
// of fields it changed).

import { signedFetch } from "./devicekey.js";
import { normalizeApiOrigin } from "./hostaccess.js";

export const SURFACE = "browser_overlay";

// This surface renders every control accepted by the v1 API.
export const SUPPORTED_CONTROLS = new Set(["radio", "checkbox", "approve_deny", "dropdown", "chat", "confirm_rows"]);

/**
 * Build the POST /v1/requests/{request_id}/answer body for a given control
 * and the human's selection.
 *   radio / approve_deny: selection is a single option id (string).
 *   checkbox: selection is an array of option ids.
 * Throws for a control this client does not support.
 */
export function buildAnswerPayload({ control, selection, answeredBy, deviceId }) {
  if (!SUPPORTED_CONTROLS.has(control)) {
    throw new Error(`unsupported control: ${control}`);
  }
  if (control === "checkbox" || control === "confirm_rows") {
    if (!Array.isArray(selection) || selection.some((id) => typeof id !== "string" || !id) || new Set(selection).size !== selection.length) {
      throw new Error(`${control} answer requires an array of distinct option ids`);
    }
    if (control === "confirm_rows" && !selection.length) throw new Error("Select at least one row to confirm");
  } else if (control === "chat") {
    if (typeof selection !== "string" || !selection.trim() || selection.length > 4000) throw new Error("Enter a message between 1 and 4000 characters");
  } else if (typeof selection !== "string" || selection.length === 0) {
    throw new Error(`${control} answer requires a single option id`);
  }

  const body = {
    control,
    value: selection,
    surface: SURFACE,
  };
  if (answeredBy) body.answered_by = answeredBy;
  if (deviceId) body.device = deviceId;
  return body;
}

export async function postAnswer(apiBase, requestId, body, { idempotencyKey, signal } = {}) {
  apiBase = normalizeApiOrigin(apiBase);
  // Signed per device (HCA-P6, lib/devicekey.js): the server records the
  // verified device id on the answer, whatever body.device says.
  const resp = await signedFetch(`${apiBase}/v1/requests/${encodeURIComponent(requestId)}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}) },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`answer POST failed: ${resp.status} ${text}`);
  }
  return resp.json();
}

// Inbox pages remain arrays; the response header carries the next opaque
// cursor. Bound pagination and detect loops before replacing a cached inbox.
export async function fetchInbox(apiBase, { signal, fetchFn = signedFetch } = {}) {
  apiBase = normalizeApiOrigin(apiBase);
  const items = [];
  const seen = new Set();
  let cursor = null;
  for (let page = 0; page < 50; page++) {
    const query = cursor ? `?limit=200&after=${encodeURIComponent(cursor)}` : "";
    const response = await fetchFn(`${apiBase}/v1/inbox${query}`, { signal });
    if (!response.ok) { const error = new Error(`GET /v1/inbox failed: ${response.status}`); error.status = response.status; throw error; }
    const rows = await response.json();
    if (!Array.isArray(rows)) throw new Error("Invalid inbox response");
    items.push(...rows);
    cursor = response.headers?.get?.("X-HCA-Next-Cursor");
    if (!cursor) return items;
    if (seen.has(cursor)) throw new Error("Inbox pagination returned a repeated cursor");
    seen.add(cursor);
  }
  throw new Error("Inbox exceeds the supported pagination limit");
}

/**
 * Incremental SSE parser for the text/event-stream body produced by
 * api/app/inbox_api.py (format_sse in api/app/events.py): blocks of
 * "event: <name>\ndata: <json>\n\n", plus bare comment lines
 * (": connected\n\n", ": keep-alive\n\n") that carry no event.
 *
 * Call with the leftover buffer from the previous call and the newly
 * received chunk of text; returns the parsed events found in this chunk
 * and the new leftover buffer (a partial block that has not seen its
 * terminating blank line yet).
 */

/**
 * The data: line of each SSE block is the full publish envelope from
 * api/app/events.py's format_sse/publish: {event, data, timestamp}, with
 * the actual CollectionRequest or AnswerRecord nested under .data, not at
 * the top level (the top-level "event" key duplicates the SSE "event:"
 * line's name; it is not the record). This unwraps that one extra layer.
 */
// HCA-P14 surface routing: request.created and request.escalated envelopes
// carry visible_to, the device ids the event applies to right now. A record
// without the field (older server) is treated as addressed to everyone.
export function isAddressedTo(record, deviceId) {
  const list = record && record.visible_to;
  if (!Array.isArray(list)) return true;
  return !!deviceId && list.includes(deviceId);
}

export function unwrapStreamEnvelope(parsedData) {
  if (parsedData && typeof parsedData === "object" && "data" in parsedData) {
    return parsedData.data;
  }
  return parsedData;
}

export function parseSSEChunk(buffer, chunkText) {
  const combined = buffer + chunkText;
  const blocks = combined.split("\n\n");
  // The last element is either "" (combined ended on a full block) or a
  // partial block awaiting more data; keep it as the new buffer.
  const newBuffer = blocks.pop();
  const events = [];

  for (const block of blocks) {
    if (block.startsWith(":")) {
      // a bare comment (connected / keep-alive); carries no event.
      continue;
    }
    let eventName = null;
    let dataLine = null;
    let id = null;
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice("event:".length).trim();
      } else if (line.startsWith("data:")) {
        dataLine = line.slice("data:".length).trim();
      } else if (line.startsWith("id:")) {
        // HCA-P20: the events row id; handed back as Last-Event-ID on
        // reconnect so the server replays what this device missed.
        id = line.slice("id:".length).trim();
      }
    }
    if (eventName && dataLine !== null) {
      try {
        events.push({ event: eventName, data: JSON.parse(dataLine), id });
      } catch (e) {
        // A malformed data line is dropped, not thrown, so one bad event
        // does not take down the whole stream reader.
        events.push({ event: eventName, data: null, parseError: String(e), id });
      }
    }
  }

  return { events, buffer: newBuffer };
}

// HCA-P7: Google sign-in linked to this device.
export async function fetchSelfDevice(apiBase) {
  apiBase = normalizeApiOrigin(apiBase);
  const resp = await signedFetch(`${apiBase}/v1/devices/self`);
  if (!resp.ok) throw new Error(`GET /v1/devices/self ${resp.status}`);
  return resp.json();
}

// HCA-P14 priority controls. The ladder places the owner can pick; the
// numbers are the server's priority values (lower is earlier).
export const PRIORITY_CHOICES = Object.freeze([
  { value: 10, label: "Primary: asks show here first" },
  { value: 100, label: "Default: shares the ladder with other default devices" },
  { value: 200, label: "Fallback: asks reach here only after the primary's window" },
]);

export function priorityLabel(priority) {
  const hit = PRIORITY_CHOICES.find((c) => c.value === Number(priority));
  return hit ? hit.label : `Custom (${priority})`;
}

export async function setSelfPriority(apiBase, priority) {
  apiBase = normalizeApiOrigin(apiBase);
  const payload = JSON.stringify({ priority: Number(priority) });
  const resp = await signedFetch(`${apiBase}/v1/devices/self`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: payload,
  });
  if (!resp.ok) {
    let detail = "";
    try { detail = (await resp.json()).detail || ""; } catch (_) { /* no body */ }
    throw new Error(`set priority ${resp.status}${detail ? `: ${detail}` : ""}`);
  }
  return resp.json();
}

export async function linkGoogle(apiBase, accessToken) {
  apiBase = normalizeApiOrigin(apiBase);
  const payload = JSON.stringify({ access_token: accessToken });
  const resp = await signedFetch(`${apiBase}/v1/devices/self/google-link`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: payload,
  });
  if (!resp.ok) {
    let detail = "";
    try { detail = (await resp.json()).detail || ""; } catch (_) { /* no body */ }
    throw new Error(`google link ${resp.status}${detail ? `: ${detail}` : ""}`);
  }
  return resp.json();
}
