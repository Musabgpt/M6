"use strict";

// NewAl Mobile UI. All server text is inserted with textContent, never innerHTML.
const store = {
  get(key) { try { return localStorage.getItem(key) || ""; } catch (_) { return ""; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* private mode */ } },
  remove(key) { try { localStorage.removeItem(key); } catch (_) { /* private mode */ } },
};
const state = {
  token: store.get("newal_token"),
  runId: store.get("newal_run") || null,
  runStatus: null,
  rendered: 0,
  busy: false,
  polling: false,
  mode: "ask",
  todosOpen: true,
};
const pendingButtons = new Map();
const $ = (id) => document.getElementById(id);
const TOOL_NAMES = {
  list_dir: "List", glob_files: "Glob", read_file: "Read", search_text: "Search",
  propose_edit: "Edit", propose_write: "Write", run_command: "Bash", todo_write: "Tasks",
};
const COMMANDS = [
  ["/new", "start a new session"],
  ["/sessions", "open past sessions"],
  ["/changes", "review proposed and applied changes"],
  ["/mode", "switch: ask before edits / apply edits"],
  ["/files", "browse the workspace"],
  ["/clear", "clear the screen (keeps the session)"],
  ["/help", "show commands"],
];
const STATUS_LABELS = {
  running: "working", awaiting_approval: "needs you", done: "ready", failed: "failed", stopped: "stopped",
};
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ----- small DOM helpers ---------------------------------------------------

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function button(label, className, onClick) {
  const element = node("button", className || "", label);
  element.type = "button";
  element.addEventListener("click", onClick);
  return element;
}

function nearBottom() {
  const log = $("log");
  return log.scrollHeight - log.scrollTop - log.clientHeight < 120;
}

function scrollEnd() {
  $("log").scrollTop = $("log").scrollHeight;
}

function append(element) {
  const spinner = $("spinner");
  if (spinner && spinner.parentNode === $("log")) $("log").insertBefore(element, spinner);
  else $("log").appendChild(element);
  scrollEnd();
  return element;
}

function say(kind, text) {
  const message = node("div", "msg " + kind, text);
  message.dir = "auto";
  return append(message);
}

function card() {
  return append(node("div", "card"));
}

// ----- markdown (safe subset, built with DOM nodes) -------------------------

function inline(parent, text) {
  const parts = text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g);
  for (const part of parts) {
    if (!part) continue;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      parent.appendChild(node("code", "", part.slice(1, -1)));
    } else if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      parent.appendChild(node("strong", "", part.slice(2, -2)));
    } else {
      parent.appendChild(document.createTextNode(part));
    }
  }
}

function codeBlock(code) {
  const wrap = node("div", "codeblock");
  const pre = node("pre", "", code);
  const copy = button("Copy", "copy", async () => {
    try { await navigator.clipboard.writeText(code); copy.textContent = "Copied"; } catch (_) { copy.textContent = "Failed"; }
    setTimeout(() => { copy.textContent = "Copy"; }, 1500);
  });
  wrap.append(pre, copy);
  return wrap;
}

function markdown(text) {
  const root = node("div", "md");
  const chunks = String(text).split(/^```[^\n]*\n?/m);
  chunks.forEach((chunk, index) => {
    if (index % 2 === 1) {
      root.appendChild(codeBlock(chunk.replace(/\n$/, "")));
      return;
    }
    let list = null;
    let para = [];
    const flush = () => {
      if (para.length) {
        const p = node("p");
        inline(p, para.join("\n"));
        root.appendChild(p);
        para = [];
      }
    };
    for (const line of chunk.split("\n")) {
      const bullet = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
      const heading = line.match(/^#{1,4}\s+(.*)$/);
      if (bullet) {
        flush();
        if (!list) { list = node("ul"); root.appendChild(list); }
        const li = node("li");
        inline(li, bullet[1]);
        list.appendChild(li);
        continue;
      }
      list = null;
      if (heading) { flush(); const h = node("h3"); inline(h, heading[1]); root.appendChild(h); continue; }
      if (!line.trim()) { flush(); continue; }
      para.push(line);
    }
    flush();
  });
  return root;
}

function sayMarkdown(kind, text) {
  const message = node("div", "msg " + kind);
  message.dir = "auto";
  message.appendChild(markdown(text || "(empty answer)"));
  return append(message);
}

// ----- API -----------------------------------------------------------------

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

// ----- busy state and spinner ----------------------------------------------

function setBusy(on) {
  state.busy = on;
  $("send").disabled = on;
  $("send").textContent = on ? "Working..." : "Send";
  $("send").hidden = on;
  $("stop").hidden = !on;
  if (!on) setSpinner(null);
}

function setSpinner(text) {
  let spinner = $("spinner");
  if (!text) {
    if (spinner) spinner.hidden = true;
    return;
  }
  if (!spinner) {
    spinner = node("div");
    spinner.id = "spinner";
    spinner.append(node("span", "glyph", "✻"), node("span", "label"), node("span", "hint", "· tap Stop to interrupt"));
  }
  $("log").appendChild(spinner);   // always keep it last
  spinner.querySelector(".label").textContent = text;
  spinner.hidden = false;
  scrollEnd();
}

function spinnerText(snapshot) {
  const last = snapshot.events[snapshot.events.length - 1] || {};
  if (last.type === "tool_start") {
    return (TOOL_NAMES[last.name] || last.name) + (last.label ? "(" + last.label + ")" : "") + "…";
  }
  return "Thinking…";
}

// ----- rendering -------------------------------------------------------------

function resetLog() {
  $("log").textContent = "";
  pendingButtons.clear();
  state.rendered = 0;
  renderTodos([]);
}

function render(snapshot) {
  const stick = nearBottom();
  snapshot.events.slice(state.rendered).forEach(renderEvent);
  state.rendered = snapshot.events.length;
  state.runStatus = snapshot.status;
  renderTodos(snapshot.todos || []);
  $("status").textContent = snapshot.working ? "working" : (STATUS_LABELS[snapshot.status] || snapshot.status);
  $("title").lastChild.textContent = " " + (snapshot.title || "NewAl");
  const live = snapshot.working || snapshot.status === "running";
  if (live) setSpinner(spinnerText(snapshot));
  else setSpinner(null);
  if (stick || !live) scrollEnd();  // keep the newest card, including approval buttons, above the input bar
}

function renderTool(event) {
  const element = node("div", "tool" + (event.ok ? "" : " fail"));
  const head = node("div", "head");
  head.append(node("span", "dot", "●"), node("span", "name", TOOL_NAMES[event.name] || event.name));
  if (event.label) head.appendChild(node("span", "arg", "(" + event.label + ")"));
  if (event.auto_applied) head.appendChild(node("span", "badge", "applied"));
  element.appendChild(head);
  if (event.summary) {
    const sub = node("div", "sub", event.summary);
    sub.dir = "auto";
    element.appendChild(sub);
  }
  if (event.change_id) {
    const row = node("div", "row");
    row.appendChild(button("Review change", event.auto_applied ? "" : "primary", () => openDiff(event.change_id)));
    element.appendChild(row);
  }
  append(element);
}

function renderEvent(event) {
  switch (event.type) {
    case "user":
      say("user", event.text);
      break;
    case "tool":
      renderTool(event);
      break;
    case "note":
      sayMarkdown("assistant note", event.text);
      break;
    case "approval_requested": {
      const element = card();
      element.appendChild(node("div", "title", "Run this command in Termux?"));
      element.appendChild(node("pre", "box", event.command));
      const row = node("div", "row");
      const allow = button("Allow once", "primary", () => resolveApproval(event.approval_id, true, false));
      const always = button("Allow always", "", () => resolveApproval(event.approval_id, true, true));
      always.title = "Allow this exact command for the rest of this session";
      const deny = button("Deny", "danger", () => resolveApproval(event.approval_id, false, false));
      row.append(allow, always, deny);
      element.appendChild(row);
      pendingButtons.set(event.approval_id, [allow, always, deny]);
      break;
    }
    case "approval_resolved":
      (pendingButtons.get(event.approval_id) || []).forEach((b) => { b.disabled = true; });
      say("muted", event.allowed
        ? (event.always ? "Command approved (always, this session) and finished." : "Command approved and finished.")
        : "Command denied.");
      break;
    case "auto_approved":
      say("muted", "Allowed for this session: " + event.command);
      break;
    case "final":
      sayMarkdown("assistant", event.text);
      break;
    case "error":
      say("error", event.text);
      break;
    case "stopped":
      say("muted", "⏹ Stopped by you. Send a message to continue.");
      break;
    default:
      break;  // model, thinking, tool_start, todos: shown by the spinner and task panel
  }
}

function renderTodos(todos) {
  const panel = $("todoPanel");
  panel.textContent = "";
  if (!todos.length) { panel.hidden = true; return; }
  panel.hidden = false;
  const done = todos.filter((t) => t.status === "completed").length;
  const head = node("div", "t-head");
  head.append(node("span", "", "Tasks " + done + "/" + todos.length), node("span", "", state.todosOpen ? "▾" : "▸"));
  head.addEventListener("click", () => { state.todosOpen = !state.todosOpen; renderTodos(todos); });
  panel.appendChild(head);
  if (!state.todosOpen) return;
  for (const todo of todos) {
    const row = node("div", "todo " + todo.status);
    row.dir = "auto";
    row.append(node("span", "", todo.status === "completed" ? "☒" : todo.status === "in_progress" ? "◐" : "☐"),
      node("span", "", todo.content));
    panel.appendChild(row);
  }
}

// ----- running the agent -----------------------------------------------------

async function poll() {
  if (state.polling) return;
  state.polling = true;
  setBusy(true);
  try {
    for (;;) {
      const snapshot = await api("GET", "/api/runs/" + state.runId);
      render(snapshot);
      if (!snapshot.working && snapshot.status !== "running") break;
      await sleep(snapshot.working ? 500 : 250);
    }
  } catch (error) {
    say("error", error.message);
  } finally {
    state.polling = false;
    setBusy(false);
  }
}

function selectRun(runId) {
  state.runId = runId;
  if (runId) store.set("newal_run", runId);
  else store.remove("newal_run");
}

async function sendGoal(goal) {
  setBusy(true);
  try {
    const canContinue = state.runId && ["done", "failed", "stopped"].includes(state.runStatus);
    if (canContinue) {
      render(await api("POST", "/api/runs/" + state.runId + "/messages", { text: goal }));
    } else {
      const run = await api("POST", "/api/runs", { goal });
      resetLog();
      selectRun(run.id);
      render(run);
    }
    await api("POST", "/api/runs/" + state.runId + "/advance?background=1", {});
  } catch (error) {
    say("error", error.message);
    setBusy(false);
    return;
  }
  await poll();
}

async function resolveApproval(approvalId, allow, always) {
  (pendingButtons.get(approvalId) || []).forEach((b) => { b.disabled = true; });
  setBusy(true);
  try {
    await api("POST", "/api/runs/" + state.runId + "/approval?background=1",
      { approval_id: approvalId, allow, always });
  } catch (error) {
    say("error", error.message);
  }
  await poll();
}

async function stopRun() {
  if (!state.runId) return;
  $("stop").disabled = true;
  try {
    render(await api("POST", "/api/runs/" + state.runId + "/cancel", {}));
  } catch (error) {
    say("error", error.message);
  } finally {
    $("stop").disabled = false;
  }
}

async function openRun(runId) {
  try {
    const snapshot = await api("GET", "/api/runs/" + runId);
    resetLog();
    selectRun(runId);
    render(snapshot);
    if (snapshot.working || snapshot.status === "running") poll();
  } catch (error) {
    if (error.status === 404) selectRun(null);
    else say("error", error.message);
  }
}

function newSession() {
  selectRun(null);
  state.runStatus = null;
  resetLog();
  $("title").lastChild.textContent = " NewAl";
  $("status").textContent = "";
  say("muted", "New session. Describe what you want to build or fix.");
}

// ----- changes ---------------------------------------------------------------

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
    say("error", "That change no longer exists (changes are kept until the server restarts).");
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
  $("changesDlg").close();
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

async function openChanges() {
  const list = $("changeList");
  list.textContent = "";
  $("changesDlg").showModal();
  try {
    const changes = (await api("GET", "/api/changes")).changes;
    if (!changes.length) list.appendChild(node("p", "sub", "No changes yet."));
    for (const change of changes.slice().reverse()) {
      const item = button("", "list-item", () => openDiff(change.id));
      item.append(node("span", "", change.path), node("span", "meta", change.kind + " · " + change.status));
      list.appendChild(item);
    }
  } catch (error) {
    list.appendChild(node("div", "err-text", error.message));
  }
}

// ----- sessions --------------------------------------------------------------

function ago(seconds) {
  const diff = Math.max(0, Date.now() / 1000 - seconds);
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + " min ago";
  if (diff < 86400) return Math.floor(diff / 3600) + " h ago";
  return Math.floor(diff / 86400) + " d ago";
}

async function openSessions() {
  const list = $("sessionList");
  list.textContent = "";
  $("sessionsDlg").showModal();
  try {
    const runs = (await api("GET", "/api/runs")).runs;
    if (!runs.length) list.appendChild(node("p", "sub", "No sessions yet."));
    for (const run of runs) {
      const item = button("", "list-item" + (run.id === state.runId ? " current" : ""), () => {
        $("sessionsDlg").close();
        openRun(run.id);
      });
      const title = node("span", "", run.title);
      title.dir = "auto";
      item.append(title, node("span", "meta", run.status + " · " + run.turns + " message(s) · " + ago(run.updated)));
      list.appendChild(item);
    }
  } catch (error) {
    list.appendChild(node("div", "err-text", error.message));
  }
}

// ----- settings --------------------------------------------------------------

function showMode() {
  const auto = state.mode === "accept_edits";
  $("btnMode").textContent = auto ? "⏵⏵ apply edits" : "⏸ ask before edits";
  $("btnMode").classList.toggle("auto", auto);
}

async function toggleMode() {
  const next = state.mode === "accept_edits" ? "ask" : "accept_edits";
  try {
    state.mode = (await api("POST", "/api/settings", { mode: next })).mode;
    showMode();
    say("muted", state.mode === "accept_edits"
      ? "Edits are now applied right away (you can still Undo each one). Commands still ask first."
      : "Edits now wait for your review.");
  } catch (error) {
    say("error", error.message);
  }
}

// ----- files -----------------------------------------------------------------

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

async function openFiles() {
  const tree = $("tree");
  tree.textContent = "";
  $("filesDlg").showModal();
  try { await loadTree(".", tree, 0); } catch (error) { tree.appendChild(node("div", "err-text", error.message)); }
}

// ----- slash commands --------------------------------------------------------

function runCommand(text) {
  const name = text.trim().split(/\s+/)[0].toLowerCase();
  switch (name) {
    case "/new": newSession(); return true;
    case "/sessions": openSessions(); return true;
    case "/changes": openChanges(); return true;
    case "/mode": toggleMode(); return true;
    case "/files": openFiles(); return true;
    case "/clear": resetLog(); say("muted", "Screen cleared. The session continues; /sessions shows it again."); return true;
    case "/help":
      say("muted", COMMANDS.map(([cmd, help]) => cmd.padEnd(11) + help).join("\n"));
      return true;
    default:
      return false;
  }
}

function updateSlashMenu() {
  const value = $("goal").value;
  const menu = $("slashMenu");
  menu.textContent = "";
  if (!value.startsWith("/") || value.includes(" ") || value.includes("\n")) { menu.hidden = true; return; }
  const matches = COMMANDS.filter(([cmd]) => cmd.startsWith(value.toLowerCase()));
  if (!matches.length) { menu.hidden = true; return; }
  for (const [cmd, help] of matches) {
    const item = button("", "", () => {
      $("goal").value = "";
      menu.hidden = true;
      runCommand(cmd);
    });
    item.append(node("span", "", cmd), node("span", "", help));
    menu.appendChild(item);
  }
  menu.hidden = false;
}

function autosize() {
  const area = $("goal");
  area.style.height = "auto";
  area.style.height = Math.min(area.scrollHeight, 160) + "px";
}

// ----- boot ------------------------------------------------------------------

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
    state.mode = info.mode || "ask";
    showMode();
    say("muted", "Workspace: " + info.workspace + "\nProviders: " + info.providers.join(", ") +
      (info.project_notes ? "\nProject notes: NEWAL.md loaded" : "") + "\nType /help for commands.");
    if (state.runId) await openRun(state.runId);
  } catch (error) {
    if (error.status === 401) {
      state.token = "";
      store.remove("newal_token");
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
  store.set("newal_token", value);
  $("gate").close();
  await boot();
});

$("btnFiles").addEventListener("click", openFiles);
$("btnSessions").addEventListener("click", openSessions);
$("newSession").addEventListener("click", () => { $("sessionsDlg").close(); newSession(); });
$("btnMode").addEventListener("click", toggleMode);
$("stop").addEventListener("click", stopRun);
$("goal").addEventListener("input", () => { autosize(); updateSlashMenu(); });

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const goal = $("goal").value.trim();
  if (!goal || state.busy) return;
  $("goal").value = "";
  autosize();
  $("slashMenu").hidden = true;
  if (goal.startsWith("/") && runCommand(goal)) return;
  sendGoal(goal);
});

for (const [buttonId, dialogId] of [["closeFiles", "filesDlg"], ["closeViewer", "viewerDlg"], ["closeDiff", "diffDlg"],
  ["closeSessions", "sessionsDlg"], ["closeChanges", "changesDlg"]]) {
  $(buttonId).addEventListener("click", () => $(dialogId).close());
}

boot();
