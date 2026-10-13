"use strict";

// NewAl Mobile UI. All server text is inserted with textContent, never innerHTML.
const state = {
  token: localStorage.getItem("newal_token") || "",
  runId: null,
  rendered: 0,
  busy: false,
};
const pendingButtons = new Map();
const $ = (id) => document.getElementById(id);

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function scrollEnd() {
  $("log").scrollTop = $("log").scrollHeight;
}

function say(kind, text) {
  const message = node("div", "msg " + kind, text);
  $("log").appendChild(message);
  scrollEnd();
}

function card() {
  const element = node("div", "card");
  $("log").appendChild(element);
  scrollEnd();
  return element;
}

function button(label, className, onClick) {
  const element = node("button", className || "", label);
  element.type = "button";
  element.addEventListener("click", onClick);
  return element;
}

async function api(method, path, body) {
  const headers = { Authorization: "Bearer " + state.token };
  const init = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  let data = {};
  try { data = await response.json(); } catch (_) { /* non-JSON error page */ }
  if (!response.ok) {
    const error = new Error(data.error || "HTTP " + response.status);
    error.status = response.status;
    throw error;
  }
  return data;
}

function setBusy(on) {
  state.busy = on;
  $("send").disabled = on;
  $("send").textContent = on ? "Working..." : "Send";
}

function render(snapshot) {
  snapshot.events.slice(state.rendered).forEach(renderEvent);
  state.rendered = snapshot.events.length;
  $("status").textContent = snapshot.status;
  scrollEnd();  // keep the newest card, including approval buttons, above the input bar
}

function renderEvent(event) {
  switch (event.type) {
    case "model":
      say("muted", "Model " + event.provider + " - step " + event.step +
        (event.tool_calls ? ", " + event.tool_calls + " tool call(s)" : ""));
      break;
    case "tool": {
      const element = card();
      element.appendChild(node("div", "", (event.ok ? "OK  " : "Err ") + event.name +
        (event.path ? "  " + event.path : "")));
      if (event.summary) element.appendChild(node("div", "sub", event.summary));
      if (event.change_id) {
        const row = node("div", "row");
        row.appendChild(button("Review change", "primary", () => openDiff(event.change_id)));
        element.appendChild(row);
      }
      break;
    }
    case "approval_requested": {
      const element = card();
      element.appendChild(node("div", "", "Approve this command to run it in Termux?"));
      element.appendChild(node("pre", "box", event.command));
      if (event.risk) element.appendChild(node("div", "risk", "⚠ " + event.risk));
      const row = node("div", "row");
      const allow = button("Allow once", "primary", () => resolveApproval(event.approval_id, true));
      const deny = button("Deny", "danger", () => resolveApproval(event.approval_id, false));
      row.append(allow, deny);
      element.appendChild(row);
      pendingButtons.set(event.approval_id, [allow, deny]);
      break;
    }
    case "approval_resolved":
      (pendingButtons.get(event.approval_id) || []).forEach((b) => { b.disabled = true; });
      say("muted", event.allowed ? "Command approved and finished." : "Command denied.");
      break;
    case "final":
      say("assistant", event.text || "(empty answer)");
      break;
    case "error":
      say("error", event.text);
      break;
    case "context_compacted":
      say("muted", "Older steps were summarized to fit the model's context.");
      break;
    case "continuity_correction":
      say("muted", "The model lost track of the task; it was reminded once.");
      break;
    default:
      break;
  }
}

async function sendGoal(goal) {
  say("user", goal);
  setBusy(true);
  try {
    const run = await api("POST", "/api/runs", { goal });
    state.runId = run.id;
    state.rendered = 0;
    render(await api("POST", "/api/runs/" + run.id + "/advance", {}));
  } catch (error) {
    say("error", error.message);
  } finally {
    setBusy(false);
  }
}

async function resolveApproval(approvalId, allow) {
  (pendingButtons.get(approvalId) || []).forEach((b) => { b.disabled = true; });
  setBusy(true);
  try {
    render(await api("POST", "/api/runs/" + state.runId + "/approval",
      { approval_id: approvalId, allow }));
  } catch (error) {
    say("error", error.message);
  } finally {
    setBusy(false);
  }
}

async function openDiff(changeId) {
  let changes = [];
  try {
    changes = (await api("GET", "/api/changes")).changes;
  } catch (error) {
    say("error", error.message);
    return;
  }
  const change = changes.find((c) => c.id === changeId);
  if (!change) {
    say("error", "That change no longer exists.");
    return;
  }
  $("diffTitle").textContent = change.path + "  (" + change.status + ")";
  const body = $("diffBody");
  body.textContent = "";
  for (const line of (change.diff || "(no textual difference)").split("\n")) {
    let className = "";
    if (line.startsWith("+++") || line.startsWith("---")) className = "sub";
    else if (line.startsWith("+")) className = "add";
    else if (line.startsWith("-")) className = "del";
    else if (line.startsWith("@@")) className = "hunk";
    body.appendChild(node("span", className, line + "\n"));
  }
  const actions = $("diffActions");
  actions.textContent = "";
  if (change.status === "pending") {
    actions.append(
      button("Accept", "primary", () => decideChange(changeId, "accept")),
      button("Reject", "danger", () => decideChange(changeId, "reject")),
    );
  } else if (change.status === "accepted") {
    actions.append(button("Undo", "danger", () => decideChange(changeId, "revert")));
  }
  $("diffDlg").showModal();
}

async function decideChange(changeId, action) {
  $("diffDlg").close();
  try {
    const result = await api("POST", "/api/changes/" + changeId + "/" + action, {});
    say(result.ok ? "muted" : "error", result.output);
  } catch (error) {
    say("error", error.message);
  }
}

async function loadTree(path, into, depth) {
  const result = await api("GET", "/api/tree?path=" + encodeURIComponent(path));
  if (!result.ok) {
    into.appendChild(node("div", "err-text", result.output));
    return;
  }
  for (const raw of result.output.split("\n")) {
    if (!raw || raw === "(empty)" || raw.startsWith("...(")) continue;
    const isDir = raw.endsWith("/");
    const name = isDir ? raw.slice(0, -1) : raw;
    const childPath = path === "." ? name : path + "/" + name;
    const item = node("button", "tree-item" + (isDir ? " dir" : ""), (isDir ? "+ " : "  ") + name);
    item.type = "button";
    item.style.paddingInlineStart = (8 + depth * 14) + "px";
    into.appendChild(item);
    if (isDir) {
      const box = node("div", "");
      box.hidden = true;
      into.appendChild(box);
      let open = false;
      item.addEventListener("click", async () => {
        open = !open;
        item.textContent = (open ? "- " : "+ ") + name;
        if (open && !box.childElementCount) {
          try { await loadTree(childPath, box, depth + 1); } catch (e) { say("error", e.message); }
        }
        box.hidden = !open;
      });
    } else {
      item.addEventListener("click", () => openFile(childPath));
    }
  }
}

async function openFile(path) {
  try {
    const result = await api("GET", "/api/file?path=" + encodeURIComponent(path));
    $("viewerTitle").textContent = path;
    $("viewerBody").textContent = result.output;
    $("viewerDlg").showModal();
  } catch (error) {
    say("error", error.message);
  }
}

function showGate(message) {
  $("gateError").textContent = message || "";
  $("gate").showModal();
}

async function boot() {
  if (!state.token) {
    showGate();
    return;
  }
  try {
    const info = await api("GET", "/api/status");
    say("muted", "Workspace: " + info.workspace + "\nProviders: " + info.providers.join(", "));
  } catch (error) {
    if (error.status === 401) {
      state.token = "";
      localStorage.removeItem("newal_token");
      showGate("Token rejected. Paste the current token.");
    } else {
      say("error", error.message);
    }
  }
}

$("saveToken").addEventListener("click", async () => {
  const value = $("tokenInput").value.trim();
  if (!value) {
    showGate("Paste the token first.");
    return;
  }
  state.token = value;
  localStorage.setItem("newal_token", value);
  $("gate").close();
  await boot();
});

$("btnFiles").addEventListener("click", async () => {
  const tree = $("tree");
  tree.textContent = "";
  $("filesDlg").showModal();
  try { await loadTree(".", tree, 0); } catch (error) { tree.appendChild(node("div", "err-text", error.message)); }
});

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const goal = $("goal").value.trim();
  if (!goal || state.busy) return;
  $("goal").value = "";
  sendGoal(goal);
});

for (const [buttonId, dialogId] of [["closeFiles", "filesDlg"], ["closeViewer", "viewerDlg"], ["closeDiff", "diffDlg"]]) {
  $(buttonId).addEventListener("click", () => $(dialogId).close());
}

boot();
