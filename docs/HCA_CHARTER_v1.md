---
title: Human Collections Agent (HCA), charter v1
summary: An independent app and API that collects what a human must decide or supply, rendered front and center over any UI, first design the factory cockpit.
---

# Human Collections Agent (HCA), charter v1

Ruled by Al 2026-09-29 09:39 PT in the factory administrator chat. Rendered by andy. Canon home: this file in the new repository human-collections-agent (factory space); a pointer PR in fpcore-migration-agent.

## Why

The factory's universal "glow" (MARKER_ANDY_FOCUS, NowScreen: the house beacon ring on the control the human should look at next) tells the human where to look. It does not bring the need to the human. HCA does: the request appears over the page, animated, zoomed in, with the controls to answer it, and returns the answer to the caller. Al's rule stands: the human is asked with easy selections (radio, checkbox, approve and deny, dropdown, chat), never open text where a selection would do; every choice carries the suggested path and why plus the value of each other option. If the human cannot find the control, the design is not functional.

## What it is

1. An independent web app (its own repository, its own deploy) that mounts as an overlay above every page of a host UI through one script tag or one import; the host never re-renders for it.
2. A collection API the host calls when the human arrives: POST a collection request (what is needed, from whom, by when, the controls, the context to zoom into, the suggested path and the alternatives); the API renders it, collects the answer, and returns it to the caller (webhook, long poll, or WebSocket) as a typed record with who answered, when, on which surface, and the exact control used.
3. An API document written for other AI systems: every control in the library, its JSON shape, its answer shape, its validation, and a worked example, so any agent can ask a human the same way.
4. Voice as an option on both sides: text to voice reads the request; voice to text fills a text answer or picks a selection; both off by default and switchable per request and per human.

## Surfaces

HCA delivers requests across a fallback ladder of surfaces operating over a single unified API. When a host page or security policy blocks the primary overlay, delivery falls back sequentially:

1. **Browser Overlay**: SSE connection with Chrome alarm fallback for real-time rendering directly over the host page.
2. **Browser Notification**: System-level browser notification alerting the user to open the collection card.
3. **macOS Notification**: Native macOS desktop notification presenting OS-fitted actions.
4. **Dedicated macOS App**: Standalone macOS menu bar app for dedicated task management and collection responses.

Each surface provides an OS-fitted visual presentation and action controls while maintaining identical request payloads, option structures, and signed answer records.

## Control library v1 (at least as flexible as the current chat widgets)

radio (one of n, each option with label, description, suggested flag and why), checkbox (many of n), approve_deny (one decision, red-gate fields resource, change, impact, exact command, rollback rendered inside the card when present), dropdown (long option lists, searchable), chat (free text with history, only when a selection cannot do), confirm_row (a list of rows each with its own confirm), path_hint (route, screen, control label, button label the request refers to, rendered as a clickable breadcrumb), plus a zoom panel that shows the underlying data the decision is about (a readiness row, a diff, a screenshot) without leaving the page.

## Answer record

request_id, control, value(s), answered_by (identity and role), answered_at, surface (browser_overlay, browser_notification, macos_notification, macos_app, web), path_taken (screens and controls used before answering), duration_ms, voice_used, then the caller's own passthrough. The record is also written to the UI/UX usage log (handoff:uiux-usage-log) and its separate audit.

## First integration

The factory cockpit: readiness report rows (Confirm proposals), the NEXT ask, gate closes and red gates render through HCA front and center, with the glow retained as the fallback for hosts that do not load the overlay.

## Roles

Maya plans (architecture, API contract, control library spec, deploy shape) and Dale reads. Hank holds the build go-ahead; builder2 is proposed as the HCA builder so Hank's hands stay on the cockpit anomaly fixes (packet:console-review-20260929, D1 to D10 and N1 to N12) in parallel. Al merges and approves deploys. andy sequences, creates nothing in the repo without a PR, and renders rulings into canon.

## Decisions applied

Design rulings applied on 2026-09-29:

1. **2026-09-29**: Overlay transport is SSE with Chrome alarm fallback.
2. **2026-09-29**: Red-gate request unanswered at deadline_at is automatically denied with an explicit Deny answer record.
3. **2026-09-29**: Fallback ladder of surfaces on one API in order: browser overlay, browser notification, macOS notification, dedicated macOS app, each with OS-fitted look and actions while request, options, and answer record stay identical.
4. **2026-09-29**: Added POST /v1/requests/{request_id}/cancel with a reason.
5. **2026-09-29**: Added GET /v1/inbox listing pending requests sorted by deadline and impact.
6. **2026-09-29**: Added GET /v1/inbox/stream as text/event-stream pushing created, answered, cancelled, and expired events.
7. **2026-09-29**: Added GET /v1/audit with filters for agent, from, to, surface, and outcome returning answer records with surface and device.
8. **2026-09-29**: Creation shape and bootstrap source: The HCA agent itself is a clone of the Andy Superagent (cloned in the Base44 editor, refitted to the HCA seat) that bootstraps from this repository.

## Not in v1

Payments, identity federation beyond the factory's own session, mobile native shells.
