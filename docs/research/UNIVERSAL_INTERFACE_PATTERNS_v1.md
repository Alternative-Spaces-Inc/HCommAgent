# Research: Universal Interface Patterns v1 for HCA

## Summary

The Human Collections Agent (HCA) provides a universal, human-in-the-loop collection surface designed to bridge background automation workflows and human decision-makers. Grounded in Al's rulings (2026-09-27 and 2026-09-29) and the HCA Charter v1, this document specifies the interface patterns, widget grammar, delivery constraints, escalation mechanisms, and audit structures required for reliable human collections. The core principle of HCA is frictionless interaction: humans are presented with structured, low-cognitive-load selections (Approve/Deny, single-choice option cards, radio lists, checkboxes) rather than open text fields whenever a structured choice is possible. Every collection request contains a explicit recommendation, an explanation of why that recommendation was chosen, a detailed description of alternative choices explaining when each alternative fits, and precise path hints mapping directly to the host application's navigation and UI controls. HCA operates across multiple delivery surfaces, starting with a Chrome Extension Manifest V3 browser overlay, and extending to text/messaging, voice, and standalone web channels.

## Vocabulary

This vocabulary is extracted directly from the HCA Charter v1 (docs/HCA_CHARTER_v1.md) and OpenAPI specification (api/openapi.yaml). These schema object names and terms are reused verbatim throughout HCA specifications and implementations:

- CollectionRequest: The primary payload submitted by background automations or host systems to request a human decision or input. It encapsulates caller, audience, title, why, path_hint, zoom, control, options_or_fields, suggested, red_gate, voice, deadline_at, and deliver_to.
- RequestReceipt: The initial acknowledgement returned by HCA upon accepting a CollectionRequest, containing request_id and current state (queued, shown, answered, expired, withdrawn).
- Option: A structured selection choice within a control, consisting of id, label, and description (where description articulates the value of the option and when it fits).
- PathHint: A structured breadcrumb mapping the exact location of the target control in the host UI, containing route, screen, control_label, and button_label.
- AnswerRecord: The immutable, typed record generated when a human responds to a request, returning request_id, control, value, answered_by, answered_role, answered_at, surface, path_taken, duration_ms, voice_used, and passthrough.
- ControlSpec: The machine-readable metadata defining a control type in the library, including control name, request_schema, answer_schema, and example payload.
- audit record: The structured entry dispatched to the UI/UX usage log (handoff:uiux-usage-log) and security audit trail capturing decision provenance, duration, surface, and navigation path.

## Pattern catalogue

### Pattern 1: Human-in-the-Loop Approval Gates

#### Problem
Background agents and automated pipelines frequently reach critical decision points (such as production deployments, schema migrations, or financial transfers) that require human authorization. Without structured approval gates, systems either block indefinitely, crash, or risk executing unauthorized high-impact operations.

#### Pattern
Implement durable, stateful interrupt and signal gates that halt execution, serialize workflow state, and present a structured approval request to designated humans. The workflow remains suspended until a cryptographically verifiable answer is returned or a timeout condition triggers.

#### How HCA Applies It
HCA accepts a CollectionRequest containing a control of type approve_deny or red_gate. The underlying backend suspends the calling agent's task state (or polls via webhook/long poll/WebSocket). HCA renders an elevated decision card containing red-gate impact details (resource, change, impact, exact_command, rollback) directly over the active host screen. Upon approval or denial, HCA constructs a signed AnswerRecord, resumes the caller, and logs the action to handoff:uiux-usage-log.

#### Anti-Pattern to Avoid
Open-ended prompt boxes asking "What should the system do next?" or unverified webhook callbacks that lack audit trails, path traces, or exact command visibility.

### Pattern 2: Browser Overlay and Decision Cards (Chrome Extension MV3)

#### Problem
Forcing human operators to switch contexts, open separate administration dashboards, or search through email/Slack channels to approve background tasks introduces significant latency and cognitive friction.

#### Pattern
Inject an unobtrusive browser overlay driven by a Chrome Extension Manifest V3 service worker that displays interactive decision cards directly over whatever web page the operator is currently viewing.

#### How HCA Applies It
HCA utilizes a Chrome Extension MV3 background service worker, extension badges, native browser notifications, and content script overlays. When a CollectionRequest arrives for the active user role, the extension increments the badge counter and pops up a focused decision card overlay. The overlay presents the request title, why summary, path_hint breadcrumbs, suggested option card, and alternative choices without re-rendering or disrupting the underlying page DOM.

#### Anti-Pattern to Avoid
Persistent background pages (deprecated in MV3) or heavy content scripts that mutate host page styles and re-render host application DOM structures.

### Pattern 3: Accessible Selection Controls (WAI-ARIA)

#### Problem
Custom decision widgets and popups often fail keyboard navigation, screen reader accessibility, and clarity guidelines, leading to operator error or inability to operate via assistive technologies.

#### Pattern
Adhere strictly to WAI-ARIA Authoring Practices for Radio Groups, Buttons, and Disclosure panels, ensuring full keyboard focus management, ARIA roles, and unambiguous visually distinct selection states.

#### How HCA Applies It
All HCA UI controls (radio, checkbox, approve_deny, dropdown) use native semantic HTML elements augmented with WAI-ARIA attributes (role="radiogroup", role="radio", aria-checked, aria-describedby). Button styling matches the factory cockpit design tokens with high-contrast active states. Keyboard focus is trapped inside modal decision cards, and Space/Enter keys trigger actions predictably.

#### Anti-Pattern to Avoid
Non-semantic div elements with custom click listeners that lack keyboard focus handlers, ARIA states, or screen reader descriptions.

### Pattern 4: Escalation and Timeout Routing

#### Problem
Human decision-makers may be away, busy, or unresponsive, causing background automation workflows to stall indefinitely.

#### Pattern
Define explicit timeout windows, multi-stage reminder ladders, delegation rules, and safe default fallback behaviors for every decision request.

#### How HCA Applies It
Every CollectionRequest accepts deadline_at. HCA enforces an automated escalation ladder: sending periodic notifications on the primary surface (Chrome overlay badge), escalating to secondary surfaces (SMS/text or push) after key thresholds, delegating to backup roles, and finally executing the pre-configured safe default action (e.g., expire or deny) when deadline_at is reached.

#### Anti-Pattern to Avoid
Indefinite hanging states without deadline limits, or defaulting to dangerous destructive actions upon request expiration.

### Pattern 5: Voice-First Single-Word Confirmation

#### Problem
Voice-enabled or mobile operators cannot efficiently navigate complex menus or speak freeform paragraphs to authorize simple operational tasks.

#### Pattern
Implement structured voice read-backs followed by single-word voice confirmations ("Approve", "Deny", "Option One") with explicit confirmation loops.

#### How HCA Applies It
When CollectionRequest.voice.read_aloud is true, HCA synthesizes a concise summary: title, why, and suggested choice. When accept_speech is true, HCA listens specifically for defined keyword responses matching Option.id or standard confirmation words. HCA reads back the detected selection ("Confirming selection: Approve. Is this correct?") and waits for a single-word confirmation ("Yes" or "Confirm") before finalizing the AnswerRecord.

#### Anti-Pattern to Avoid
Accepting freeform conversational speech for high-risk decisions without explicit read-back and single-word confirmation.

## Widget grammar for HCA

Every decision widget rendered by HCA follows a strict structural grammar designed to eliminate ambiguity and enforce complete contextual transparency.

### Core Required Fields across All Widgets
1. Title and Why: High-level summary title and plain-language explanation (why) detailing why the decision is required immediately.
2. Suggested Option (`suggested`): Contains `option_id` and an explicit `why` string justifying the recommendation.
3. Option Alternatives (`options_or_fields`): Every option carries an `id`, `label`, and `description` (which specifies the value of that option and when it fits).
4. Path Hint (`path_hint`): Exact host application location containing `route`, `screen`, `control_label`, and `button_label`.

### Widget Type Specifications

#### 1. Approve / Deny Widget (`approve_deny`)
- Purpose: Binary high-impact decisions (gate approvals, production changes).
- Structure:
  - Header: Title, caller agent ID, deadline countdown.
  - Context & Red Gate Panel (when `red_gate` is populated):
    - Resource: Targeted infrastructure or business entity.
    - Change: Summary of proposed modification.
    - Impact: Potential risk or operational effect.
    - Exact Command: Complete executable command or payload.
    - Rollback: Exact rollback plan or command.
  - Path Hint Breadcrumb: Displayed prominently at top of card.
  - Controls:
    - Primary Action Button: "Approve" (styled in standard factory cockpit primary blue/green token).
    - Secondary Action Button: "Deny" (styled in standard factory cockpit outline/secondary token).
  - Recommendation Card: Highlighted box showing suggested choice and why.

#### 2. Single Choice Widget (`radio` or `dropdown`)
- Purpose: Selecting exactly one option from a list of two or more distinct choices.
- Structure:
  - `radio` used for small option counts (2 to 5 options); rendered as interactive option cards.
  - `dropdown` used for large or searchable option lists (>5 options).
  - Every option displays label, description (when it fits), and visual badge if it is the suggested choice.
  - One decision per widget: exactly one option can be selected.

#### 3. Multi Choice Widget (`checkbox`)
- Purpose: Selecting zero, one, or multiple items from a set of independent options.
- Structure:
  - List of checkbox cards, each with `id`, `label`, `description`.
  - Suggested configuration pre-checked or clearly badge-indicated with justification.
  - Clear submit button with label "Confirm Selection".

#### 4. Acknowledge / Confirm Rows Widget (`confirm_rows`)
- Purpose: Reviewing and confirming multiple individual items or readiness rows sequentially within a single screen.
- Structure:
  - Table or list of items, each with item ID, description, status, and individual "Confirm" toggle button.
  - Summary header showing overall progress (e.g., "3 of 5 rows confirmed").
  - Final "Submit Confirmations" button enabled when required rows are confirmed.

## Delivery surfaces and their constraints

HCA is designed to operate seamlessly across multiple delivery channels, adapting to the constraints of each surface while maintaining a consistent decision contract.

### 1. Browser Overlay (Chrome Extension Manifest V3) - Primary Surface
- Architecture: Background Service Worker, Offscreen Documents, Content Scripts, Extension Badges, and Notification API.
- Constraints & Solutions:
  - Service Worker Lifetime: MV3 service workers terminate after short periods of inactivity (~30 seconds). HCA uses event-driven architecture, waking the service worker upon receiving push notifications or API polling events.
  - No Persistent Background Pages: DOM parsing or audio synthesis requiring DOM APIs are offloaded to Chrome Offscreen Documents (`chrome.offscreen`).
  - Overlay Isolation: Content scripts inject an isolated Shadow DOM container to prevent host CSS contamination or script interference.

### 2. Text / Messaging Surface (iMessage, SMS, Chat)
- Architecture: Webhook receivers parsing structured SMS/chat responses.
- Constraints & Solutions:
  - Character limits and formatting constraints: HCA formats requests into concise text summaries: Title, Why, Suggested option, and numbered choices (e.g., "1: Option A (Recommended), 2: Option B").
  - Response parsing: Accepts exact option number or single-keyword replies.

### 3. Voice / Phone Surface
- Architecture: Telephony API integrated with Text-to-Speech (TTS) and Speech-to-Text (STT) engines.
- Constraints & Solutions:
  - Audio latency and clarity: Keeps spoken prompts under 20 seconds.
  - Read-back requirement: Always reads back the selected choice and requires explicit single-word confirmation ("Yes" / "Confirm") before recording the answer.

### 4. Standalone Web Surface
- Architecture: Dedicated full-screen or embedded web application.
- Constraints & Solutions:
  - Used when the host application does not support script injection or browser extension overlay.
  - Provides zoom panel inspection views for full data diffs, screenshots, and logs alongside the decision widget.

## Escalation ladder

To ensure background tasks are never blocked indefinitely by unmonitored requests, HCA enforces a structured 4-stage escalation ladder:

1. Stage 1 (Initial Notification):
   - Trigger: CollectionRequest created.
   - Action: Renders Chrome extension badge counter, sends primary overlay card to active user session.
2. Stage 2 (First Reminder - 50% Deadline Elapsed):
   - Trigger: Time reaches 50% of duration between request creation and `deadline_at`.
   - Action: Pulsing badge animation, system browser notification dispatched.
3. Stage 3 (Secondary Channel Escalation - 75% Deadline Elapsed):
   - Trigger: Time reaches 75% of deadline.
   - Action: Escalates to secondary surfaces (dispatches SMS/Text or Mobile Push to backup audience role).
4. Stage 4 (Safe Default Expiration - 100% Deadline Elapsed):
   - Trigger: `deadline_at` timestamp reached.
   - Action: HCA automatically marks RequestReceipt state as `expired`, executes pre-configured safe fallback action (e.g., auto-deny or safe abort), generates an AnswerRecord indicating expiration, and alerts the calling agent.

## Audit record shape

Every completed, denied, or expired collection request generates a structured audit record dispatched immediately to the factory UI/UX usage log (`handoff:uiux-usage-log`) and security audit stream.

### Exact JSON Audit Record Schema

```json
{
  "request_id": "req_20260929_8f3a12",
  "caller": "agent_migration_factory_v2",
  "audience": "factory_administrator",
  "title": "Approve Database Schema Migration for Client Project",
  "control": "approve_deny",
  "value": "approve",
  "answered_by": "andy_admin",
  "answered_role": "factory_administrator",
  "answered_at": "2026-09-29T14:32:10Z",
  "surface": "web_overlay",
  "path_taken": [
    "route:/cockpit/readiness",
    "screen:ReadinessOverview",
    "control_label:MigrationGate",
    "button_label:Approve"
  ],
  "duration_ms": 4250,
  "voice_used": false,
  "red_gate_details": {
    "resource": "postgresql_cluster_main",
    "change": "ALTER TABLE client_records ADD COLUMN audit_hash VARCHAR(64);",
    "impact": "Zero-downtime schema expansion on active client project database.",
    "exact_command": "pg_migrate --config /etc/migrate.conf --apply",
    "rollback": "pg_migrate --config /etc/migrate.conf --rollback"
  },
  "passthrough": {
    "job_id": "job_99214",
    "pipeline": "prod_deploy"
  }
}
```

## Open questions for Al

The following strategic decision points regarding HCA architecture and rollout are presented for Al's ruling. Each question is structured as an HCA selection widget with options, recommendations, and path hints.

### Question 1: Extension MV3 Service Worker Polling vs. WebSockets for Real-Time Requests

- Title: Select Communication Protocol for MV3 Extension Overlay
- Why: Determine how the Chrome Extension overlay receives real-time collection requests from the HCA backend.
- Path Hint:
  - Route: `/settings/hca/transport`
  - Screen: `HCA Extension Settings`
  - Control Label: `Transport Protocol`
  - Button Label: `Save Transport Configuration`
- Control Type: `radio`
- Suggested Option: `option_sse_polling`
- Suggested Why: Server-Sent Events (SSE) with HTTP/2 combined with lightweight alarms works reliably within MV3 service worker lifecycle constraints without socket drop issues during service worker idle termination.
- Options:
  1. Option ID: `option_sse_polling`
     Label: SSE with Alarm Fallback (Recommended)
     Description: Uses Server-Sent Events while active and periodic Chrome Alarm polling when idle. Fits best with MV3 service worker lifetime limits.
  2. Option ID: `option_websockets`
     Label: Persistent WebSockets
     Description: Maintains open WebSocket connections in background. Requires frequent keep-alive signals to prevent service worker termination. Fits high-frequency instant messaging needs.
  3. Option ID: `option_long_polling`
     Label: Standard Long Polling
     Description: Simple HTTP long polling requests. High battery/network overhead but simple server implementation. Fits legacy restricted environments.

### Question 2: Default Expiration Action for High-Impact Red Gates

- Title: Select Default Safe Behavior on Request Expiration for Red Gates
- Why: Establish mandatory system behavior when a human fails to respond before `deadline_at` on high-impact decisions.
- Path Hint:
  - Route: `/settings/hca/policies`
  - Screen: `Safety Policies`
  - Control Label: `Red Gate Timeout Policy`
  - Button Label: `Update Policy`
- Control Type: `radio`
- Suggested Option: `option_auto_deny`
- Suggested Why: Automatic denial guarantees system safety by preventing unreviewed high-risk changes from proceeding without explicit human sign-off.
- Options:
  1. Option ID: `option_auto_deny`
     Label: Automatic Deny (Recommended)
     Description: Mark request expired and return explicit Deny answer record to caller. Fits zero-trust production safety.
  2. Option ID: `option_escalate_hold`
     Label: Indefinite Hold with Escalation
     Description: Keep request queued, pause pipeline indefinitely, and escalate alerts to secondary administrators. Fits non-time-critical manual reviews.

### Question 3: Standalone Web Surface Fallback Behavior

- Title: Select Fallback Mechanism when Host Application Blocks Extension Injection
- Why: Define how HCA delivers collection requests when host web pages enforce strict Content Security Policies (CSP) that block script or overlay injection.
- Path Hint:
  - Route: `/settings/hca/surfaces`
  - Screen: `Surface Configuration`
  - Control Label: `CSP Fallback Action`
  - Button Label: `Save Surface Policy`
- Control Type: `radio`
- Suggested Option: `option_standalone_tab`
- Suggested Why: Opening a dedicated HCA standalone tab/popup ensures 100% decision availability regardless of host page CSP restrictions.
- Options:
  1. Option ID: `option_standalone_tab`
     Label: Standalone HCA Tab / Dedicated Window (Recommended)
     Description: Automatically open or focus a dedicated HCA web page when overlay injection is blocked. Fits strict enterprise host security environments.
  2. Option ID: `option_fallback_notification`
     Label: Browser Notification with Direct Link
     Description: Display standard browser notification linking to HCA decision URL. Fits minimalist user notification preferences.

## Sources

1. LangGraph Human-in-the-Loop Interrupt Approval Pattern
   URL: https://medium.com/ai-simplified-in-plain-english/a-field-guide-to-human-in-the-loop-with-langgraph-ff43827e37c9
   Description: Details the stateful interrupt primitive in LangGraph for pausing agent graph execution and persisting checkpoint state until human feedback is provided.

2. Temporal Signals and Workflows for Human Approval
   URL: https://tekko.id/en/blog/building-fault-tolerant-ai-agents-with-temporal-and-langgraph
   Description: Explains Temporal Signal channels and durable timers for orchestrating long-running human-in-the-loop approval workflows with fault tolerance.

3. Slack Block Kit Interactive Approval Messages
   URL: https://www.digitalapplied.com/blog/build-ai-powered-slack-bot-tutorial-event-subscriptions-2026
   Description: Outlines structured JSON Block Kit layouts, section cards, action buttons, and interaction event callbacks for messaging approvals.

4. GitHub Environment Protection Rules and Deployment Approvals
   URL: https://hoop.dev/blog/just-in-time-action-approval-in-github-ci-cd-ultimate-control-for-safe-deployments
   Description: Describes required reviewer protection rules, environment gates, and audit trails in CI/CD pipeline deployment workflows.

5. Chrome Extension Manifest V3 Service Worker Lifetime and Offscreen Documents
   URL: https://www.groovyweb.co/blog/chrome-extension-development-guide-2026
   Description: Documents MV3 background service worker termination rules, offscreen document APIs (`chrome.offscreen`), and event-driven extension architecture.

6. WAI-ARIA Authoring Practices for Radio Groups and Accessible Buttons
   URL: https://www.deque.com/fr/blog/accessible-aria-buttons/
   Description: Technical standards for accessible keyboard focus management, ARIA roles, states, and selection controls.

7. AI Voice Agent Single-Word Confirmation and Call Protocols
   URL: https://www.salesmessage.com/blog/confirmation-call-script
   Description: Structural rules for voice agent confirmation loops, read-back accuracy tests, and single-word authorization responses.
