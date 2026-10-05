// Shared, framework-free helpers for the Human Collections Agent browser
// overlay client (background service worker and popup both import this).
//
// The endpoints and payload shapes here follow api/openapi.yaml, not the
// stale draft field names in docs/plans/HCA_CLOUD_RUN_PLAN_v1.md section 3
// (that table predates the 2026-09-29 reconciliation; see the
// reconciliation table at the top of that same plan document for the list
// of fields it changed).

import { signedFetch } from "./devicekey.js";

export const SURFACE = "browser_overlay";

// Controls this client version renders. The full control library
// (docs/HCA_CHARTER_v1.md) also lists dropdown, chat, and confirm_row;
// those are out of scope for this sub-task (dispatch hca-p3-20260929 named
// only radio, check, approve and deny) and are reported as unsupported
// rather than silently mishandled.
export const SUPPORTED_CONTROLS = new Set(["radio", "checkbox", "approve_deny"]);

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
  if (control === "checkbox") {
    if (!Array.isArray(selection)) {
      throw new Error("checkbox answer requires an array of option ids");
    }
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

export async function postAnswer(apiBase, requestId, body) {
  // Signed per device (HCA-P6, lib/devicekey.js): the server records the
  // verified device id on the answer, whatever body.device says.
  const resp = await signedFetch(`${apiBase}/v1/requests/${encodeURIComponent(requestId)}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`answer POST failed: ${resp.status} ${text}`);
  }
  return resp.json();
}

export async function fetchInbox(apiBase) {
  const resp = await signedFetch(`${apiBase}/v1/inbox`);
  if (!resp.ok) {
    throw new Error(`GET /v1/inbox failed: ${resp.status}`);
  }
  return resp.json();
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
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice("event:".length).trim();
      } else if (line.startsWith("data:")) {
        dataLine = line.slice("data:".length).trim();
      }
    }
    if (eventName && dataLine !== null) {
      try {
        events.push({ event: eventName, data: JSON.parse(dataLine) });
      } catch (e) {
        // A malformed data line is dropped, not thrown, so one bad event
        // does not take down the whole stream reader.
        events.push({ event: eventName, data: null, parseError: String(e) });
      }
    }
  }

  return { events, buffer: newBuffer };
}

// HCA-P7: Google sign-in linked to this device.
export async function fetchSelfDevice(apiBase) {
  const resp = await signedFetch(`${apiBase}/v1/devices/self`);
  if (!resp.ok) throw new Error(`GET /v1/devices/self ${resp.status}`);
  return resp.json();
}

export async function linkGoogle(apiBase, accessToken) {
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
