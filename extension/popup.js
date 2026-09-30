import { buildAnswerPayload, postAnswer, SUPPORTED_CONTROLS } from "./lib/api.js";

const CACHE_KEY = "hca_inbox_cache";
const API_BASE_KEY = "hca_api_base";
const FOCUS_KEY = "hca_focus_request_id";

function requestIdOf(item) {
  return item.request_id || item.id;
}

function optionLabel(opt) {
  const desc = opt.description ? ` (${opt.description})` : "";
  return `${opt.label}${desc}`;
}

function renderOptionsFor(control, options, suggestedId, containerId) {
  const container = document.createElement("div");
  container.className = "options";

  if (control === "approve_deny") {
    // Two prominent buttons rather than radio inputs, per
    // docs/HCA_CHARTER_v1.md's control library: "approve_deny (one
    // decision ... rendered inside the card when present)".
    const wrap = document.createElement("div");
    wrap.className = "actions";
    for (const opt of options) {
      const btn = document.createElement("button");
      btn.textContent = optionLabel(opt);
      btn.dataset.optionId = opt.id;
      btn.className = opt.id === "deny" ? "deny" : "primary";
      wrap.appendChild(btn);
    }
    container.appendChild(wrap);
    return { container, getSelection: null, buttonGroup: container.querySelector(".actions") };
  }

  const inputType = control === "checkbox" ? "checkbox" : "radio";
  for (const opt of options) {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = inputType;
    input.name = `${containerId}-choice`;
    input.value = opt.id;
    if (opt.id === suggestedId) input.checked = true;
    label.appendChild(input);
    label.append(optionLabel(opt) + (opt.id === suggestedId ? " (suggested)" : ""));
    container.appendChild(label);
  }

  const getSelection = () => {
    const inputs = [...container.querySelectorAll("input")];
    if (control === "checkbox") {
      return inputs.filter((i) => i.checked).map((i) => i.value);
    }
    const picked = inputs.find((i) => i.checked);
    return picked ? picked.value : null;
  };

  return { container, getSelection, buttonGroup: null };
}

async function submitAnswer(apiBase, requestId, control, selection, cardEl, errorEl) {
  errorEl.textContent = "";
  try {
    const body = buildAnswerPayload({ control, selection });
    await postAnswer(apiBase, requestId, body);
    const stored = await chrome.storage.local.get(CACHE_KEY);
    const cache = stored[CACHE_KEY] || {};
    delete cache[requestId];
    await chrome.storage.local.set({ [CACHE_KEY]: cache });
    cardEl.remove();
  } catch (e) {
    errorEl.textContent = e.message || String(e);
  }
}

function renderCard(apiBase, item, isFocused) {
  const requestId = requestIdOf(item);
  const card = document.createElement("div");
  card.className = "card" + (item.red_gate ? " red-gate" : "");
  card.id = `hca-card-${requestId}`;

  const title = document.createElement("div");
  title.className = "title";
  title.textContent = item.title;
  card.appendChild(title);

  if (item.why) {
    const why = document.createElement("div");
    why.className = "why";
    why.textContent = item.why;
    card.appendChild(why);
  }

  if (item.suggested && item.suggested.why) {
    const suggested = document.createElement("div");
    suggested.className = "suggested";
    suggested.textContent = `Suggested: ${item.suggested.why}`;
    card.appendChild(suggested);
  }

  const control = item.control;
  const errorEl = document.createElement("div");
  errorEl.className = "error";

  if (!SUPPORTED_CONTROLS.has(control)) {
    const note = document.createElement("div");
    note.className = "unsupported";
    note.textContent = `This client does not yet render the "${control}" control. Answer from another surface.`;
    card.appendChild(note);
    return card;
  }

  const { container, getSelection, buttonGroup } = renderOptionsFor(
    control,
    item.options_or_fields || [],
    item.suggested && item.suggested.option_id,
    requestId
  );
  card.appendChild(container);

  if (buttonGroup) {
    for (const btn of buttonGroup.querySelectorAll("button")) {
      btn.addEventListener("click", () => submitAnswer(apiBase, requestId, control, btn.dataset.optionId, card, errorEl));
    }
  } else {
    const actions = document.createElement("div");
    actions.className = "actions";
    const submitBtn = document.createElement("button");
    submitBtn.className = "primary";
    submitBtn.textContent = "Submit";
    submitBtn.addEventListener("click", () => submitAnswer(apiBase, requestId, control, getSelection(), card, errorEl));
    actions.appendChild(submitBtn);
    card.appendChild(actions);
  }

  card.appendChild(errorEl);
  if (isFocused) card.style.outline = "2px solid #2a6fdb";
  return card;
}

async function render() {
  const container = document.getElementById("cards");
  container.innerHTML = "";

  const stored = await chrome.storage.local.get([CACHE_KEY, API_BASE_KEY, FOCUS_KEY]);
  const apiBase = stored[API_BASE_KEY];
  const cache = stored[CACHE_KEY] || {};
  const focusId = stored[FOCUS_KEY];
  const items = Object.values(cache);

  if (!apiBase) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "Set the API base URL in the extension's options page first.";
    container.appendChild(empty);
    return;
  }

  if (items.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "Nothing pending.";
    container.appendChild(empty);
    return;
  }

  for (const item of items) {
    container.appendChild(renderCard(apiBase, item, requestIdOf(item) === focusId));
  }

  if (focusId) {
    await chrome.storage.local.remove(FOCUS_KEY);
  }
}

render();
