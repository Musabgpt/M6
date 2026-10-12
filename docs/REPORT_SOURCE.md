> Historical report from the uploaded source archive; its repository delivery claims refer to the earlier NewAl-Cloud project, not M6.

# MUSAB MAX — EXECUTION REPORT (revision 2)
Project: NewAl Mobile. Goal: a phone version of the NewAl-Cloud work, as a Cursor-style workflow with the phone
browser, its apps and Termux ("حقق الهدف كامل").
Protocol: MUSAB MAX — ST☆ Universal Engineering Protocol v3.0 (uploaded file, read in full).

## 1. Objective & Verified Scope
- Original objective: rebuild the NewAl-Cloud work as a complete phone app with the Cursor-style workflow.
- Delivered in this revision:
  - an original Python engine for Termux (stdlib only): propose-review-accept file workflow with undo,
    approval-gated commands with secret redaction, a bounded agent loop, privacy-tier-aware provider fallback,
    loopback-only token-protected HTTP;
  - a mobile browser UI, installable to the home screen (web app manifest), verified in a real Chromium at a phone
    viewport.
- Not delivered, with the reason in each case:
  - Android APK or Termux runtime verified on a device. Environment: no Android SDK, no Gradle, no device
    (probe table in VALIDATION.md).
  - Real model calls. Environment: model providers unreachable from the sandbox.
  - An exact copy of Cursor. Declined (ADR-001): its code, prompt, branding and assets belong to its owner.

## 2. Research & Design
- Repository findings MX-A01 to MX-A06 (docs/FINDINGS.md): CI pulls mutable external source; the phone server's
  shared-key protection is unverified; the release gate is a grep; command approval in the Java runner is
  unverified; two parallel Android modules; self-modifying autonomy code not reviewed.
- Options and decision: ADR-002 in docs/DECISIONS.md (Python engine in Termux plus browser UI chosen over the
  Kotlin app, which cannot be built or run here).

## 3. Implementation
- Engine: newal_mobile/{server, agent, tools, providers, config, sandbox, prompt, __main__, __init__}.py
- UI: ui/{index.html, app.js, manifest.webmanifest, icon.svg}
- Tests: tests/{test_tools, test_agent, test_providers_config, test_server, test_loopback_integration,
  test_hardening, helpers}.py; tests/e2e/{browser_mobile_e2e.py, ui_smoke.mjs, mock_model.py, package*.json,
  artifacts/*.png}
- Setup and docs: termux/setup.sh, config/providers.example.json, README.md, docs/*.md
- Revision 2 changes: secret redaction on command output (default); undo for accepted changes; per-run lock and
  no toolbox lock during command runs; manifest and icon; CSP manifest-src; approval card scroll fix; tap highlight
  fix; placeholder fix; a real-browser test.

## 4. GRILL & Adversarial Review
- Objections raised in revision 2 and their outcome:
  1. "Secrets reach the model through command output" → redaction by default; residual risk stated (MX-B06).
  2. "Approval buttons are hidden on a phone" → found in a screenshot, fixed, and now asserted (MX-B11).
  3. "A model call blocks every other request" → per-run lock; tested by timing (MX-B08).
  4. "Undo could destroy a later edit" → undo refuses when the file changed since the change (tested).
  5. "The manifest is blocked by the strict CSP" → manifest-src added; header tested (MX-B13).
- Counterexamples tried in revision 2: secrets in command output (several formats), undo after an external edit,
  undo of a pending or already-undone change, create-run and approval-run during a long model call, file listing
  during a long command.

## 5. MUSAB MAX Findings
- Open BLOCKERS: MX-B04 (no Android or Termux run; environment cannot close it).
- Open CRITICAL: none. MX-B01 (secret files) is FIXED.
- Open or partial MAJOR: MX-B03 (real model, environment), MX-B06 (redaction is best-effort, policy to confirm),
  MX-B09 (device layout, partial), MX-B13 (home-screen install on a device, untested), repository MX-A01 to MX-A04, MX-A06.
- Register: docs/FINDINGS.md (22 entries).

## 6. Verification
- Environment: Ubuntu 24, Python 3.12.3, Node 22.22.2, Playwright 1.56.0 with a cached Chromium.
- Unit, integration, regression: 68 tests OK (ResourceWarning as error).
- Mutation check: 10 injected defects, all caught; clean baseline OK.
- Real browser at 390x844 with touch: 14 checks pass; screenshots reviewed.
- jsdom: 10 checks pass from a clean install.
- Security and performance: loopback-only binding confirmed by a real non-loopback connection test; responsiveness
  verified by timing tests. No load test and no security scanner run.
- Not executed: device, Termux, Android browser, real model, fuzz, property-based, security scanner, independent review.

## 7. Reproducibility
- Versions: Python 3.12.3, Node 22.22.2, npm 10.9.7, Playwright 1.56.0 (dev only), jsdom 24.x (dev only).
  No runtime dependencies on the phone.
- Commands: see docs/VALIDATION.md.
- Seeds: not applicable.

## 8. Residual Risks & Limitations
- Command output is redacted by patterns, not guaranteed (MX-B06).
- Free-route data handling is unverified (MX-B03).
- Token kept in browser localStorage, protected by CSP and textContent rendering (MX-B10, accepted).
- Runs are in memory and do not survive a restart (MX-B07, accepted).
- Streaming replies are not implemented.
- Assumptions: Termux has Python 3.10+ and bash; the user reads each command before tapping Allow.
- Claims NOT made: "works with free models", "runs on Android", "as good as Cursor", "secure against a
  malicious local app that knows the token".

## 9. Final Verdict
Verdict 1, for the original request as stated (complete phone app, Cursor-style, Android/Termux, real models):
**MUSAB MAX-FAIL**.
Blockers: MX-B04 (Android and Termux not executed; environment) and MX-B03 (real model not verified; environment).
Both need the user's device and network. Exact Cursor copy is out of scope by decision (ADR-001), not a defect.

Verdict 2, for the narrowed scope "NewAl Mobile v0.1 engine and browser UI, verified in desktop Chromium at a
390x844 touch viewport with loopback mock models", explicitly excluding: Android device or Termux runtime, real
provider calls, guaranteed secret protection (redaction is best-effort), and home-screen install on a device:
**MUSAB MAX-PASS**.
This is not a release for phone use. The exclusions above are open.

## 10. Next Practical Action
1. On the phone, in Termux: `sh termux/setup.sh`, then the unit suite. Expect 68 OK (T-PHONE-1).
2. In Android Chrome: open http://127.0.0.1:8787, paste the token, run the three flows and check that Allow and
   Deny are reachable (T-PHONE-2). Then "Add to Home screen" (T-PHONE-4).
3. Confirm the redaction policy for MX-B06: keep best-effort redaction, or also block commands on free routes.
4. With network and a key: T-REAL-1 and T-REAL-2. Closes MX-B03.
5. Repository: the work is committed on a local branch `feature/newal-mobile-engine` (one commit at the tip of the branch, folder `mobile/`), NOT pushed. This sandbox has no GitHub write credentials. Publish it with the bundle or the patch in outputs (section 11).

## 11. Repository delivery
- GitHub: branch `feature/newal-mobile-engine` was created on Musabgpt/NewAl-Cloud from main (9794eee) through the
  GitHub connector. main is untouched. The branch currently points at main, so it does not yet contain this work.
- Why the code is not on GitHub yet: the connector accepts file contents only as text that is typed into the call.
  The work is 36 files and about 170 KB. Re-sending it through the connector cannot be checked reliably in one session.
  A direct `git push` from a machine with credentials sends it in one step.
- Publish the commit with one of these (the commit is one commit on top of main, in folder `mobile/`, plus
  `.github/workflows/mobile-engine.yml`):
  - newal-mobile-engine.bundle: `git fetch /path/newal-mobile-engine.bundle feature/newal-mobile-engine:feature/newal-mobile-engine`
    then `git push origin feature/newal-mobile-engine`.
  - newal-mobile-engine.patch: `git am newal-mobile-engine.patch` on a branch from main, then push.
- CI: the workflow `mobile-engine` runs on push to this branch (and on manual dispatch). It runs the 68 engine tests,
  installs Chromium, runs the real-browser phone-viewport check and the jsdom test, and uploads the screenshots as the
  artifact `mobile-screenshots`. The workflow passes actionlint. Its result has NOT been observed: the connector has no
  Actions tool and the public API is rate-limited from this sandbox. Check the Actions tab after the push.
- Nothing was merged into main, and no pull request was opened (opening one would also trigger the repository's existing
  Android workflow).
