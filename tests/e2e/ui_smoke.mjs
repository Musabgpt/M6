// Drives the real ui/app.js inside jsdom against the real NewAl Mobile server and the mock model.
// Not part of the Python unit suite. Run: npm install && npm test
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const root = process.env.NEWAL_ROOT || join(here, "..", "..");  // project root; override to run from a copy
const TOKEN = "ui-e2e-token-0123456789abcdefghijklmnop";
const MODEL_PORT = 18901;
const APP_PORT = 18902;
const base = `http://127.0.0.1:${APP_PORT}/`;

const stateDir = mkdtempSync(join(tmpdir(), "nm-state-"));
const workspace = mkdtempSync(join(tmpdir(), "nm-ws-"));
writeFileSync(join(workspace, "app.py"), "def a():\n    return 1\n");
writeFileSync(join(stateDir, "token"), TOKEN + "\n", { mode: 0o600 });
writeFileSync(join(stateDir, "providers.json"), JSON.stringify([{
  name: "mock-local", base_url: `http://127.0.0.1:${MODEL_PORT}/v1`, model: "mock", privacy_tier: "local",
}]));

const procs = [
  spawn("python3", [join(here, "mock_model.py"), String(MODEL_PORT)], { stdio: "ignore" }),
  spawn("python3", ["-m", "newal_mobile", "--port", String(APP_PORT), "--workspace", workspace],
    { cwd: root, env: { ...process.env, NEWAL_STATE_DIR: stateDir }, stdio: "ignore" }),
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(check, what, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try { if (await check()) return; } catch (_) { /* not ready yet */ }
    await sleep(150);
  }
  throw new Error("timed out waiting for: " + what);
}
function assert(condition, message) {
  if (!condition) throw new Error("ASSERTION FAILED: " + message);
  console.log("  ok - " + message);
}

let dom;
try {
  await waitFor(async () => (await fetch(base)).ok, "server to start");
  const html = readFileSync(join(root, "ui", "index.html"), "utf8");
  dom = new JSDOM(html, {
    url: base, runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(window) {
      window.localStorage.setItem("newal_token", TOKEN);
      window.fetch = (input, init) => fetch(new URL(String(input), base), init);
      window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      window.HTMLDialogElement.prototype.close = function () { this.open = false; };
    },
  });
  const doc = dom.window.document;
  const $ = (id) => doc.getElementById(id);
  const buttons = (label) => [...doc.querySelectorAll("button")].filter((b) => b.textContent === label);
  const logText = () => $("log").textContent;
  const idle = () => !$("send").disabled;
  const submit = (goal) => {
    $("goal").value = goal;
    $("composer").dispatchEvent(new dom.window.Event("submit", { cancelable: true }));
  };
  const onDisk = (name) => readFileSync(join(workspace, name), "utf8");

  console.log("Boot and connect");
  await waitFor(() => logText().includes("Workspace:"), "boot status line");
  assert(logText().includes(workspace), "UI shows the workspace path after connecting with the stored token");

  console.log("Flow 1: propose an edit, review the diff, accept it");
  submit("change return 1 to return 2");
  await waitFor(() => idle() && buttons("Review change").length > 0, "a proposed change");
  assert(onDisk("app.py").includes("return 1"), "file is untouched while the change is only proposed");
  buttons("Review change")[0].click();
  await waitFor(() => $("diffDlg").open, "diff dialog");
  assert($("diffBody").textContent.includes("+    return 2"), "diff dialog shows the added line");
  assert($("diffBody").textContent.includes("-    return 1"), "diff dialog shows the removed line");
  buttons("Accept")[0].click();
  await waitFor(() => onDisk("app.py").includes("return 2"), "accepted change written to disk");
  assert(onDisk("app.py").includes("return 2"), "accepting writes the change to the workspace");
  await waitFor(() => logText().includes("applied change"), "acceptance confirmation");
  assert(logText().includes("Proposed the change for your review."), "final model answer is shown");

  console.log("Flow 2: command waits for approval, then runs when allowed");
  submit("run echo approved");
  await waitFor(() => idle() && buttons("Allow once").length > 0, "approval card");
  assert(!existsSync(join(workspace, "approved.txt")), "command has not run before the user taps Allow");
  assert([...doc.querySelectorAll("pre.box")].some((p) => p.textContent === "echo done > approved.txt"),
    "approval card shows the exact command");
  buttons("Allow once").at(-1).click();
  await waitFor(() => logText().includes("Command approved and finished."), "approval confirmation");
  assert(existsSync(join(workspace, "approved.txt")), "approved command actually ran");

  console.log("Flow 3: a denied command never runs");
  const denyBefore = buttons("Deny").length;
  submit("run echo deny");
  await waitFor(() => idle() && buttons("Deny").length > denyBefore, "deny card");
  buttons("Deny").at(-1).click();
  await waitFor(() => logText().includes("Command denied."), "denial confirmation");
  assert(!existsSync(join(workspace, "denied.txt")), "denied command did not run");

  console.log("\nUI E2E PASSED (jsdom, real server, mock model)");
} catch (error) {
  console.error("\nUI E2E FAILED:", error.message);
  process.exitCode = 1;
} finally {
  procs.forEach((p) => p.kill());
  if (dom) dom.window.close();
}
