# NewAl Mobile

A coding agent that runs on your Android phone inside Termux. You open it in the phone browser.
The engine uses only the Python standard library, so there is nothing to compile.

## What it does
- Chat with a model about your workspace (`~/workspace`) and browse its files.
- Undo an accepted change, as long as the file has not changed since.
- Command output is redacted for common secret formats before the model sees it (best-effort).
- The agent reads files, searches text and **proposes** edits. You review each diff and accept or reject it.
- Shell commands run **only after you tap Allow** on the exact command shown.
- Works with any OpenAI-compatible chat endpoint. Fallback never silently moves data to a different privacy tier.

## What it does not do (yet)
- It is not a copy of Cursor. It is an original app with a similar workflow (see docs/DECISIONS.md).
- No Android APK, no streaming output, runs are kept in memory only.
- Not verified against any real provider in this build. See docs/VALIDATION.md.

## Run it on the phone
1. Install Termux (from F-Droid or its GitHub releases) and clone this repository as `~/M6` using `git clone https://github.com/Musabgpt/M6.git ~/M6`.
2. `cd ~/M6 && sh termux/setup.sh`
3. `python -m newal_mobile --port 8787`
4. Open `http://127.0.0.1:8787` in the phone browser.
5. Paste the token printed in the file `~/.newal-mobile/token` (run `cat ~/.newal-mobile/token`).

Optional providers: copy `config/providers.example.json` to `~/.newal-mobile/providers.json`, set the
`api_key_env` variables (for example `export GROQ_API_KEY=...`), and fill in the current model IDs.
Providers whose key is not set are skipped.

## Tests
- Engine, HTTP, integration: `python -m unittest discover -s tests -t .` (68 tests)
- Real browser at a phone viewport (needs `pip install playwright` and a Chromium: `playwright install chromium`,
  or set `CHROMIUM_PATH`): `python3 tests/e2e/browser_mobile_e2e.py`. Screenshots go to `tests/e2e/artifacts/`.
- jsdom browser test (dev only): `cd tests/e2e && npm install && npm test`
- What is verified and what is not: `docs/VALIDATION.md`

## Install to the home screen
Open the page in Chrome for Android, then choose "Add to Home screen". It opens as its own window. This is not
yet tested on a device (see docs/VALIDATION.md, T-PHONE-4).

## Security in one paragraph
The server listens on 127.0.0.1 only and rejects foreign Host headers. Every API call needs a bearer token
stored with owner-only permissions. Paths are confined to the workspace, including symlinks. Files that look
like secrets (`.env`, keys, credentials) are never read or edited by the agent. Full details: docs/SECURITY.md.

## CI on GitHub
`.github/workflows/mobile-engine.yml` runs the engine test suite and browser checks on pushes and pull requests to `main`.

## Release scope
This repository is the source code for a Termux-hosted mobile web app, **not an Android APK**.
It has not yet been tested on a real Android device or with a real external model endpoint.
No promises are made about unlimited free model usage.

## عربي — تشغيل سريع
من تطبيق Termux نفّذ الأوامر التالية:

```sh
pkg install -y git python
git clone https://github.com/Musabgpt/M6.git ~/M6
cd ~/M6
sh termux/setup.sh
python -m newal_mobile --port 8787
```

ثم افتح `http://127.0.0.1:8787` من متصفح الهاتف، وأدخل رمز الاتصال من `~/.newal-mobile/token`.
التطبيق واجهة ويب تعمل محلياً، وليس ملف APK، ويجب اختبار الاتصال الفعلي بمزود النماذج على جهازك.
