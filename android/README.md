# M6 Android APK

**A native Android installable APK, not a PWA shortcut.** It opens the M6 HTML/JS UI in a locked-down WebView and invokes the *bundled Python agent engine* through Termux's official permission-gated `RUN_COMMAND` background service. It never opens a separate terminal window as part of normal startup.

## Requirements (one-time)

1. Install the compatible Termux app from F-Droid or the official Termux GitHub release, **not** its outdated Play Store version.
2. Install Python inside Termux once: `pkg install -y python` (already installed on most existing setups).
3. In Termux `~/.termux/termux.properties`, set `allow-external-apps = true`, then run `termux-reload-settings`.
4. Install M6 APK and grant **Run commands in Termux environment** to M6 in Android Settings > Apps > M6 > Permissions > Additional permissions (or accept the system permission request).
5. Launch M6. The app installs/updates the engine in Termux, starts it in the background, and opens its own WebView. After first setup no Termux window should be necessary for day-to-day use.

The Android app **is not a self-contained Python runtime**. Termux remains an explicit, mandatory backend. The APK includes `newal_mobile/` and `ui/` but does not embed all Python/Termux binaries. On some Android versions or Termux builds, the user may need to adjust system settings. See [official RUN_COMMAND documentation](https://github.com/termux/termux-app/wiki/RUN_COMMAND-Intent).

## Security boundary

- No Android Accessibility control, screen-reading, unrestricted device control, or automatic all-permission grant.
- M6 requires `com.termux.permission.RUN_COMMAND`, which grants powerful code execution **inside the user's Termux environment**, not system-level root access. Explicit Android permission and Termux's `allow-external-apps` gate are mandatory.
- Startup command runs as a **background** Termux command. It never asks Termux to open its terminal Activity.
- APK serves its bundled engine over a temporary random-path `127.0.0.1` socket. The Termux Python installer checks its SHA-256 before extracting it into a versioned private Termux directory. No remote download or APK-side shell interpreter is involved.
- WebView navigates only to `http://127.0.0.1:8787/`; the pre-existing Python engine requires a bearer token for API routes. Native Android passes the per-install token to the backend, stores it in Android private preferences, then initializes WebView localStorage. Android and Termux are separate app identities: the token is passed through the permissioned startup intent.
- If another service occupies TCP port 8787, the backend may not start. If Termux or the process is killed, return to M6 and tap retry. For detailed errors check `~/.m6/engine.log` inside Termux.

## Building

This is a regular Gradle Android project. In GitHub Actions, `android-apk.yml` packages the Python engine and UI into `app/src/main/assets/engine.zip` and builds an **installable, debug-signed APK**. No private signing keys are required. Download `M6-android-debug-apk` from the successful workflow run; unzip the uploaded artifact to obtain `app-debug.apk`.

For a local build with Android SDK, JDK 17 and Gradle 8.9:

```sh
python3 -m unittest discover -s tests -t .
python3 android/package_engine.py
gradle -p android --no-daemon :app:assembleDebug
```

Output: `android/app/build/outputs/apk/debug/app-debug.apk`.

**Not yet verified on a physical phone:** runtime permission dialogs, Samsung background execution, end-to-end Termux dispatch, and providers needing account keys. A CI green check proves compilation and automated tests, not that every device and model works.
