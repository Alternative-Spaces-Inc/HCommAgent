# HCA design decisions (Al, 2026-09-29, web chat widgets)

1. Transport for the browser overlay: SSE with alarm fallback (13:52 PT). Alternatives declined: persistent WebSockets, long polling.
2. Red-gate deadline with no answer: Automatic Deny, explicit Deny answer record to the caller (13:52 PT). Alternative declined: hold and escalate.
3. Fallback when a host page blocks the overlay (13:55 PT, Al's own answer, not one of the two offered): a ladder of surfaces on the same API, in this order: (1) browser notification, (2) macOS notification, (3) a dedicated macOS app. Each surface has its own look and its own set of actions fitted to the OS; the request, options and answer record are identical across surfaces. Declined as the sole fallback: a standalone HCA tab. Consequence: the charter's surfaces section and the OpenAPI need a surface field on the request and the answer record, and the plan gains a macOS notification client and a macOS app as later build items.
4. Add POST /v1/requests/{id}/cancel with a reason; surfaces drop the card, audit keeps the cancellation (13:56 PT). Alternative declined: expiry only.
5. Add GET /v1/inbox listing every pending request across agents and surfaces, sorted by deadline and impact (13:57 PT). Alternative declined: push only.
6. Add GET /v1/inbox/stream (SSE) pushing new, answered and cancelled requests to every open surface (13:58 PT). Alternative declined: surfaces poll the inbox.
