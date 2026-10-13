# M6 Android APK — standalone

**A self-contained Android app. Termux is not needed.** The APK embeds a real CPython 3.12
(via [Chaquopy](https://chaquo.com/chaquopy/)) and runs the same `newal_mobile` engine inside the app.
The web UI is shown in a locked-down WebView that only talks to `127.0.0.1:8787`.

## Install
1. Open the latest successful **Android APK** run in GitHub Actions and download `M6-standalone-debug-apk`.
2. Unzip it and install `app-debug.apk` (allow "install unknown apps" for your browser or file manager).
3. Open M6. The engine starts inside the app and the chat opens. No setup, no token to paste.

## Where things live
- Workspace: `Android/data/ai.musab.m6/files/workspace` (visible to file managers that can open app folders).
- Settings, token and task checkpoints: app-private storage.
- Optional providers: put a `providers.json` in the app's `engine-home` folder (see `config/providers.example.json`).
  Keyless free routes work by default.

## Limits (honest)
- Commands run with Android's `/system/bin/sh` inside the app sandbox. There is **no** `git`, `python` CLI,
  `pkg` or `node`; the read-only `git_status` tool reports that git is missing. For full Linux tools, use the
  Termux mode from the main README.
- The engine runs while the app is alive. Android may stop it in the background; reopening the app restarts it.
- The APK is debug-signed, ABIs `arm64-v8a` (phones) and `x86_64` (emulator). It is roughly 30–40 MB.
- Not yet verified on a physical phone. CI proves it builds and contains the runtime, not that every device works.

## Security boundary
- No Accessibility service, no device control, no extra permissions beyond `INTERNET`.
- Engine binds to 127.0.0.1 only and needs a per-install random bearer token, kept in app-private preferences.
- Shell commands still need your tap on **Allow** for each exact command, with a warning for risky ones.

## Build locally
Needs JDK 17, Android SDK, Gradle 8.9 and Python 3.12 on the build machine:

```sh
python3 -m unittest discover -s tests -t .
python3 android/package_engine.py
gradle -p android --no-daemon :app:assembleDebug
```

Output: `android/app/build/outputs/apk/debug/app-debug.apk`.
