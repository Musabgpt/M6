# Validation record (revision 2)

Environment for everything marked EXECUTED: Ubuntu 24 Linux sandbox, Python 3.12.3, Node 22.22.2, npm 10.9.7,
Playwright (Python) 1.56.0 driving the Chromium binary already cached in this sandbox (run with --no-sandbox).

NOT available here: an Android device, Termux, the Android browser, Android SDK or Gradle downloads, and any
model provider. Reachability was probed in this session:

| Host | Result | Consequence |
|---|---|---|
| api.kilo.ai, api.groq.com, openrouter.ai | HTTP 000 (blocked) | No real model call is possible here |
| dl.google.com, services.gradle.org | HTTP 000 (blocked) | No Android SDK or Gradle, so no APK can be built here |
| github.com | HTTP 200 | Repository read works; push needs a separate check |

## Evidence levels (protocol §10)
- E2 isolated tests: EXECUTED. 68 unit and integration tests (ResourceWarning as error).
- E3 integration over real sockets: EXECUTED. Loopback mock model with the real provider client, agent loop and HTTP API.
- E3 live process: EXECUTED. `python -m newal_mobile` probed with curl.
- E3 socket binding: EXECUTED. Loopback connects; the machine's non-loopback address is refused.
- E4 real browser at phone viewport: EXECUTED. Chromium, 390x844, device scale 2, touch, mobile flag, real server,
  mock model: 14 checks pass. Screenshots reviewed (tests/e2e/artifacts/).
- E4 jsdom: EXECUTED. 10 checks pass (real app.js, not a real browser engine).
- E4 Android Chrome on a device: NOT_EXECUTED.
- E4 real model: NOT_EXECUTED (blocked, see probe table).

## Commands and results (final run of revision 2)
| Check | Command | Result |
|---|---|---|
| Compile | `python3 -m py_compile newal_mobile/*.py tests/*.py` | OK |
| Unit + integration | `python3 -W error::ResourceWarning -m unittest discover -s tests -t .` | Ran 68 tests, OK |
| Real browser, phone viewport | `CHROMIUM_PATH=<chromium> python3 tests/e2e/browser_mobile_e2e.py` | exit 0, 14 checks |
| jsdom (clean install in /tmp) | `NEWAL_ROOT=<project> node ui_smoke.mjs` | exit 0, 10 checks |
| Frontend and config syntax | `node --check ui/app.js`, `sh -n termux/setup.sh`, JSON parse of manifest and providers example | OK |
| Live process | `python3 -m newal_mobile` + curl | 401 without token, 200 with token, static files 200, default route fails visibly |
| Loopback only | connect to 127.0.0.1 and to the machine's non-loopback address | loopback OK, other address refused |

## Browser checks (real Chromium, 390x844, touch)
Connect; no horizontal overflow; propose, review, accept; undo restores the file; approval card shows the exact
command; Allow and Deny are visible above the input bar; approved command runs; denied command does not run;
file viewer shows app.py; web app manifest served; no console errors or CSP violations.

## Mutation check (protocol C10 / L11)
Defects were injected into a scratch copy, one at a time. The suite must catch each:

| Injected defect | Result |
|---|---|
| M1 conflict check disabled (accept) | FAILED (failures=1) |
| M2 approval gate bypassed | FAILED (failures=4, errors=1) |
| M3 path escape check removed | FAILED (failures=3) |
| M4 privacy tier ignored on fallback | FAILED (failures=1) |
| M5 repeat-loop guard disabled | FAILED (failures=1) |
| M6 sensitive-file guard disabled | FAILED (failures=4) |
| M7 global lock held during model calls | FAILED (failures=1) |
| M8 command-output redaction disabled | FAILED (failures=1) |
| M9 undo conflict check disabled | FAILED (failures=1) |
| M10 command run under the toolbox lock | FAILED (failures=1) |
| Clean baseline | OK |

Caveat: ten hand-picked mutants. This is not a full mutation run, and no mutation tool was executed.

## What the suites prove, and what they do not
- Proves: the engine enforces its rules in code (sandbox, approval, propose-accept, undo, budgets, privacy tiers,
  token auth, redaction), and the browser UI drives those rules correctly at a phone viewport in Chromium.
- Does not prove: that a real model uses the tools well (mock models replay fixed rules); Android Chrome behaviour
  or touch on a real device; Termux bash and Python behaviour; battery and background-kill behaviour; that
  redaction catches every secret format.

## Not executed, with the step that would close each gap
- T-PHONE-1 (Termux): `sh termux/setup.sh`, then `python -m unittest discover -s tests -t .`. Expect 68 OK.
- T-PHONE-2 (Android Chrome): open http://127.0.0.1:8787, paste the token, check the layout and that Allow and Deny
  are reachable by touch. Closes most of MX-B09.
- T-PHONE-3 (second browser, e.g. Firefox for Android): repeat T-PHONE-2.
- T-PHONE-4 (home screen): use "Add to Home screen" and check that it opens standalone. Closes MX-B13 on a device.
- T-REAL-1 (network + key): set providers.json, send "list the files in my workspace", record model, reply,
  latency, and the provider's data-retention terms. Closes MX-B03.
- T-REAL-2 (network + key): ask a real model to change a function in a throwaway file. Check that it proposes,
  waits for approval, and reports honestly.
- T-BUDGET-1 (device): leave a run going with the screen off for 10 minutes to see whether Android kills the server.
- Not run: fuzzing, property-based tests, security scanner, load test, independent review.
