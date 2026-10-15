import { buildAnswerPayload } from "./lib/api.js";
import { renderControl, renderContext } from "./lib/controls.js";

// HCA-P23 Day 1: one script serves two pages. popup.html lists every pending ask; decision.html
// (body[data-page="decision"], opened by a notification click when the popup is refused) shows
// the single ask named by ?request=<id> at phone width and closes itself once that ask is gone.
const page = document.body.dataset.page === "decision" ? "decision" : "popup";
const onlyRequest = page === "decision" ? new URL(location.href).searchParams.get("request") : null;
const pending = new Set();
let generation = 0;
async function message(payload) {
  const response = await chrome.runtime.sendMessage(payload);
  if (!response?.ok) throw new Error(response?.error || "The background worker is unavailable. Reopen the popup.");
  return response;
}
function setBusy(card, busy) {
  card.setAttribute("aria-busy", String(busy));
  for (const input of card.querySelectorAll("button, input, select, textarea")) input.disabled = busy;
}
async function submit(snapshot, item, selection, card, error, reviewed) {
  const id = item.request_id || item.id;
  // Idempotent click: the first click on a card claims a token; every later click on any of
  // the card's controls is ignored until the outcome is back, whatever setBusy managed to
  // disable in between. One ask produces at most one submission per outcome.
  if (pending.has(id) || card.dataset.submitToken) return;
  const token = `${id}:${Date.now()}`;
  card.dataset.submitToken = token;
  error.textContent = "";
  try {
    if (reviewed && !reviewed.checked) throw new Error("Review the decision context and confirm that you have read it first.");
    buildAnswerPayload({ control: item.control, selection });
  } catch (failure) { error.textContent = failure.message; delete card.dataset.submitToken; return; }
  pending.add(id); setBusy(card, true);
  try {
    await message({ type: "hca_submit_answer", origin: snapshot.origin, deviceId: snapshot.deviceId, requestId: id, control: item.control, selection });
    card.remove();
    if (page === "decision") finishDecisionPage("Your answer is saved on this device and is being delivered. You can close this tab.");
  } catch (failure) { error.textContent = failure.message; setBusy(card, false); delete card.dataset.submitToken; }
  finally { pending.delete(id); }
}
function finishDecisionPage(text) {
  const container = document.getElementById("cards");
  const done = document.createElement("p"); done.className = "empty"; done.setAttribute("role", "status"); done.textContent = text;
  container.replaceChildren(done);
  setTimeout(() => window.close(), 1500);
}
function renderCard(snapshot, item) {
  const id = item.request_id || item.id;
  const card = document.createElement("section"); card.className = `card${item.red_gate ? " red-gate" : ""}`;
  // Three regions: header (the question line), body (everything long, scrolls inside the
  // card), actions (pinned; the controls a phone user must never hunt for).
  const header = document.createElement("header"); header.className = "card-header";
  const body = document.createElement("div"); body.className = "card-body";
  const actions = document.createElement("div"); actions.className = "card-actions";
  const title = document.createElement("h2"); title.id = `title-${id}`; title.textContent = item.title; card.setAttribute("aria-labelledby", title.id); header.appendChild(title);
  for (const [text, className] of [[item.why, "why"], [item.suggested?.why ? `Suggestion: ${item.suggested.why}` : null, "suggested"]]) {
    if (text) { const paragraph = document.createElement("p"); paragraph.className = className; paragraph.textContent = text; body.appendChild(paragraph); }
  }
  body.appendChild(renderContext(document, item));
  const error = document.createElement("p"); error.className = "error"; error.setAttribute("role", "alert");
  let reviewed;
  if (item.red_gate) {
    const label = document.createElement("label"); label.className = "review-confirm";
    reviewed = document.createElement("input"); reviewed.type = "checkbox"; label.append(reviewed, "I have reviewed the resource, change, impact, command and rollback."); body.appendChild(label);
  }
  try {
    const control = renderControl(document, item, id); body.appendChild(control.container);
    for (const button of control.buttons) { button.addEventListener("click", () => submit(snapshot, item, button.dataset.optionId, card, error, reviewed)); actions.appendChild(button); }
    if (control.getSelection) {
      const button = document.createElement("button"); button.className = "primary"; button.type = "button"; button.textContent = "Submit response";
      button.addEventListener("click", () => submit(snapshot, item, control.getSelection(), card, error, reviewed)); actions.appendChild(button);
    }
  } catch (failure) { error.textContent = failure.message; }
  actions.appendChild(error);
  card.append(header, body, actions);
  if (pending.has(id)) setBusy(card, true);
  if (id === snapshot.focusId || id === onlyRequest) { card.tabIndex = -1; requestAnimationFrame(() => card.focus()); }
  return card;
}
async function render() {
  const version = ++generation;
  try {
    const snapshot = await message({ type: "hca_snapshot" });
    if (version !== generation) return;
    const container = document.getElementById("cards"); container.replaceChildren();
    document.getElementById("api-origin").textContent = snapshot.origin ? `Server: ${snapshot.origin}` : "";
    const notice = document.getElementById("queue-notice");
    notice.textContent = [snapshot.queue.length ? `${snapshot.queue.length} response(s) saved on this device; delivery is retried for up to 24 hours. A saved response is not yet an accepted decision.` : "", snapshot.notice].filter(Boolean).join(" ");
    notice.hidden = !notice.textContent;
    document.getElementById("dismiss-notice").hidden = !snapshot.notice;
    const queued = new Set(snapshot.queue.map((entry) => entry.requestId));
    let items = Object.values(snapshot.cache).filter((item) => !queued.has(item.request_id || item.id));
    if (onlyRequest) items = items.filter((item) => (item.request_id || item.id) === onlyRequest);
    if (!snapshot.origin || !items.length) {
      const empty = document.createElement("p"); empty.className = "empty";
      empty.textContent = !snapshot.origin ? "Set the API server in the extension options first."
        : onlyRequest ? "This request has already been answered, cancelled or expired. Nothing to decide here."
        : "Nothing pending.";
      container.appendChild(empty);
    }
    for (const item of items) container.appendChild(renderCard(snapshot, item));
  } catch (failure) { document.getElementById("popup-error").textContent = failure.message; }
}
chrome.storage.onChanged.addListener(() => { void render(); });
void render();

document.getElementById("dismiss-notice").addEventListener("click", () => message({ type: "hca_dismiss_notice" }).then(render).catch((error) => { document.getElementById("popup-error").textContent = error.message; }));
