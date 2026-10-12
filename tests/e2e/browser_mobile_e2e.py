#!/usr/bin/env python3
"""Real-browser check with Playwright: Chromium at a phone viewport (390x844, touch, mobile flag)
against the real NewAl Mobile server and the mock model.

It is desktop Chromium emulating a phone. It is NOT Android Chrome on a device.
Run:  python3 tests/e2e/browser_mobile_e2e.py
Set CHROMIUM_PATH to a Chromium binary if Playwright's managed browser is not installed.
Screenshots are written to tests/e2e/artifacts/.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ARTIFACTS = HERE / "artifacts"
TOKEN = "browser-e2e-token-0123456789abcdefghijklmnop"
results = []


def check(condition, message):
    if not condition:
        raise AssertionError("FAILED: " + message)
    results.append(message)
    print("  ok - " + message)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url, timeout=25):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server did not start: " + url)


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    for old in ARTIFACTS.glob("*.png"):
        old.unlink()
    model_port, app_port = free_port(), free_port()
    state = tempfile.mkdtemp(prefix="nm-state-")
    workspace = tempfile.mkdtemp(prefix="nm-ws-")
    Path(state, "token").write_text(TOKEN + "\n")
    os.chmod(Path(state, "token"), 0o600)
    Path(state, "providers.json").write_text(json.dumps([{
        "name": "mock-local", "base_url": f"http://127.0.0.1:{model_port}/v1",
        "model": "mock", "privacy_tier": "local"}]))
    Path(workspace, "app.py").write_text("def a():\n    return 1\n")
    env = dict(os.environ, NEWAL_STATE_DIR=state)
    procs = [
        subprocess.Popen([sys.executable, str(HERE / "mock_model.py"), str(model_port)]),
        subprocess.Popen([sys.executable, "-m", "newal_mobile", "--port", str(app_port),
                          "--workspace", workspace], cwd=ROOT, env=env),
    ]
    base = f"http://127.0.0.1:{app_port}"
    try:
        wait_http(base + "/")
        with sync_playwright() as p:
            launch = {"headless": True, "args": ["--no-sandbox"]}
            if os.environ.get("CHROMIUM_PATH"):
                launch["executable_path"] = os.environ["CHROMIUM_PATH"]
            browser = p.chromium.launch(**launch)
            context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                          is_mobile=True, has_touch=True)
            page = context.new_page()
            problems = []
            page.on("console", lambda m: problems.append("console: " + m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))

            print("Connect from a phone-sized browser")
            page.goto(base + "/")
            page.wait_for_selector("#gate[open]")
            page.fill("#tokenInput", TOKEN)
            page.tap("#saveToken")
            page.wait_for_function("() => document.getElementById('log').textContent.includes('Workspace:')")
            check(page.is_visible("header h1"), "header and status are visible on a 390px screen")
            overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            check(overflow <= 0, f"no horizontal scrolling on the phone viewport (overflow {overflow}px)")
            page.screenshot(path=str(ARTIFACTS / "01-connected.png"))

            print("Flow 1: propose, review, accept, undo")
            page.fill("#goal", "change return 1 to return 2")
            page.tap("#send")
            page.wait_for_function(
                "() => [...document.querySelectorAll('button')].some(b => b.textContent === 'Review change')"
                " && !document.getElementById('send').disabled", timeout=20000)
            check("return 1" in Path(workspace, "app.py").read_text(),
                  "file untouched while the change is only proposed")
            page.screenshot(path=str(ARTIFACTS / "02-proposal.png"))
            page.get_by_role("button", name="Review change").first.tap()
            page.wait_for_selector("#diffDlg[open]")
            diff_text = page.locator("#diffBody").inner_text()
            check("+    return 2" in diff_text and "-    return 1" in diff_text, "diff sheet shows the change")
            page.screenshot(path=str(ARTIFACTS / "03-diff.png"))
            page.get_by_role("button", name="Accept").tap()
            page.wait_for_function("() => document.getElementById('log').textContent.includes('applied change')")
            check("return 2" in Path(workspace, "app.py").read_text(), "accepting writes the change to disk")
            page.get_by_role("button", name="Review change").first.tap()
            page.wait_for_selector("#diffDlg[open]")
            page.get_by_role("button", name="Undo").tap()
            page.wait_for_function("() => document.getElementById('log').textContent.includes('undid change')")
            check("return 1" in Path(workspace, "app.py").read_text(), "Undo restores the original file")

            print("Flow 2: approval card, allow")
            page.fill("#goal", "run echo approved")
            page.tap("#send")
            page.wait_for_selector("button:has-text('Allow once')", timeout=20000)
            page.wait_for_timeout(300)  # let the scroll settle
            allow_box = page.get_by_role("button", name="Allow once").last.bounding_box()
            composer_box = page.locator("#composer").bounding_box()
            check(allow_box is not None and allow_box["y"] + allow_box["height"] <= composer_box["y"],
                  "Allow and Deny buttons are visible above the input bar, not hidden under it")
            check(not Path(workspace, "approved.txt").exists(), "command has not run before Allow")
            card_text = page.locator("#log pre.box").last.inner_text()
            check(card_text == "echo done > approved.txt", "approval card shows the exact command")
            page.screenshot(path=str(ARTIFACTS / "04-approval.png"))
            page.get_by_role("button", name="Allow once").last.tap()
            page.wait_for_function("() => document.getElementById('log').textContent.includes('Command approved')")
            check(Path(workspace, "approved.txt").exists(), "approved command ran")

            print("Flow 3: deny")
            page.fill("#goal", "run echo deny")
            page.tap("#send")
            page.wait_for_function(
                "() => [...document.querySelectorAll('button')].filter(b => b.textContent === 'Deny').length >= 2"
                " && !document.getElementById('send').disabled", timeout=20000)
            page.get_by_role("button", name="Deny").last.tap()
            page.wait_for_function("() => document.getElementById('log').textContent.includes('Command denied.')")
            check(not Path(workspace, "denied.txt").exists(), "denied command did not run")

            print("Files drawer and viewer")
            page.tap("#btnFiles")
            page.wait_for_selector("#filesDlg[open]")
            page.locator(".tree-item", has_text="app.py").tap()
            page.wait_for_selector("#viewerDlg[open]")
            check("def a():" in page.locator("#viewerBody").inner_text(), "file viewer shows app.py")
            page.screenshot(path=str(ARTIFACTS / "05-viewer.png"))

            print("Browser-level checks")
            manifest_status = page.evaluate(
                "async () => (await fetch('/manifest.webmanifest')).status")
            check(manifest_status == 200, "web app manifest is served (home-screen install)")
            check(not problems, "no console errors or CSP violations (" + "; ".join(problems[:3]) + ")")
            browser.close()
        print(f"\nBROWSER E2E PASSED: {len(results)} checks in a real Chromium at 390x844 (touch).")
        return 0
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(state, ignore_errors=True)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(exc)
        raise SystemExit(1)
