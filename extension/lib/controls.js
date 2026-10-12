import { SUPPORTED_CONTROLS } from "./api.js";
const description = (option) => `${option.label}${option.description ? ` : ${option.description}` : ""}`;
export function renderControl(document, item, id) {
  if (!SUPPORTED_CONTROLS.has(item.control)) throw new Error(`Unsupported control: ${item.control}`);
  const container = document.createElement("fieldset");
  container.className = "options";
  const legend = document.createElement("legend");
  legend.textContent = item.control === "chat" ? "Your response" : item.control === "confirm_rows" ? "Rows to confirm" : "Choose your response";
  container.appendChild(legend);
  const options = item.options_or_fields || [];
  if (item.control === "approve_deny") {
    const actions = document.createElement("div");
    actions.className = "actions";
    for (const option of options) {
      const button = document.createElement("button");
      button.type = "button"; button.dataset.optionId = option.id;
      button.textContent = description(option); button.className = option.id === "deny" ? "deny" : "primary";
      actions.appendChild(button);
    }
    container.appendChild(actions);
    return { container, buttons: [...actions.querySelectorAll("button")], getSelection: null };
  }
  if (item.control === "chat") {
    const text = document.createElement("textarea");
    text.id = `${id}-response`; text.maxLength = 4000; text.rows = 5; text.required = true;
    const label = document.createElement("label"); label.htmlFor = text.id; label.textContent = "Message (up to 4000 characters)";
    container.append(label, text);
    return { container, buttons: [], getSelection: () => text.value };
  }
  if (item.control === "dropdown") {
    const searchLabel = document.createElement("label"); searchLabel.htmlFor = `${id}-search`; searchLabel.textContent = "Search choices";
    const search = document.createElement("input"); search.id = searchLabel.htmlFor; search.type = "search";
    const label = document.createElement("label"); label.htmlFor = `${id}-select`; label.textContent = "Selection";
    const select = document.createElement("select"); select.id = label.htmlFor; select.required = true;
    function update() {
      const value = select.value;
      select.replaceChildren();
      const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = "Select a choice…"; select.appendChild(placeholder);
      for (const option of options.filter((option) => description(option).toLowerCase().includes(search.value.toLowerCase()))) {
        const element = document.createElement("option"); element.value = option.id; element.textContent = description(option); select.appendChild(element);
      }
      select.value = [...select.options].some((option) => option.value === value) ? value : "";
    }
    search.addEventListener("input", update); update();
    const details = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = "Read all choices and their descriptions"; details.appendChild(summary);
    for (const option of options) { const paragraph = document.createElement("p"); paragraph.textContent = description(option); details.appendChild(paragraph); }
    container.append(searchLabel, search, label, select, details);
    return { container, buttons: [], getSelection: () => select.value };
  }
  const inputs = [];
  for (const option of options) {
    const label = document.createElement("label");
    const input = document.createElement("input"); input.type = ["checkbox", "confirm_rows"].includes(item.control) ? "checkbox" : "radio";
    input.name = `${id}-choice`; input.value = option.id;
    // Suggestions are information, never a preselected human decision.
    label.append(input, `${item.control === "confirm_rows" ? "Confirm: " : ""}${description(option)}${option.id === item.suggested?.option_id ? " (suggested)" : ""}`);
    container.appendChild(label); inputs.push(input);
  }
  return { container, buttons: [], getSelection: () => ["checkbox", "confirm_rows"].includes(item.control) ? inputs.filter((input) => input.checked).map((input) => input.value) : inputs.find((input) => input.checked)?.value || "" };
}
export function renderContext(document, item) {
  const container = document.createElement("div");
  if (item.red_gate) {
    const heading = document.createElement("h3"); heading.textContent = "Review before deciding"; container.appendChild(heading);
    const list = document.createElement("dl");
    for (const [key, title] of [["resource", "Resource"], ["change", "Change"], ["impact", "Impact"], ["exact_command", "Exact command"], ["rollback", "Rollback"]]) {
      const term = document.createElement("dt"); term.textContent = title;
      const definition = document.createElement("dd");
      if (key === "exact_command") { const code = document.createElement("pre"); code.textContent = item.red_gate[key] || "Not supplied"; definition.appendChild(code); }
      else definition.textContent = item.red_gate[key] || "Not supplied";
      list.append(term, definition);
    }
    container.appendChild(list);
  }
  if (item.path_hint) {
    const route = document.createElement("p"); route.textContent = `Location: ${Object.values(item.path_hint).filter(Boolean).join(" → ")}`; container.appendChild(route);
  }
  if (item.zoom) {
    const details = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = "Inspect supporting data and conversation history";
    const data = document.createElement("pre"); data.textContent = JSON.stringify(item.zoom, null, 2); details.append(summary, data); container.appendChild(details);
  }
  if (item.deadline_at) { const deadline = document.createElement("p"); deadline.textContent = `Decision deadline: ${new Date(item.deadline_at).toLocaleString()}`; container.appendChild(deadline); }
  return container;
}
