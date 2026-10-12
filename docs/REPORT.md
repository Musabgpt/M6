# M6 — Delivery and validation report

## Scope
The supplied `newal-mobile.zip` was unpacked as the source of `Musabgpt/M6`, with a Python standard-library Termux agent, mobile PWA interface, configurable free-model routes, explicit command approval, and diff review.

## Repository adjustments
- Application moved to the repository root (no extra `mobile/` directory).
- GitHub Actions workflow installed at `.github/workflows/mobile-engine.yml` and configured for `main`, PRs and manual dispatch.
- Removed captured browser screenshots from tracked source; CI regenerates them and uploads them as artifacts.
- Adjusted setup instructions to clone `Musabgpt/M6`.
- Original archive report preserved in `docs/REPORT_SOURCE.md` as historical evidence, not a current release certificate.

## Reproduced local verification
- `python -W error::ResourceWarning -m unittest discover -s tests -t .`: 68 passed on Linux (12 Oct 2026).
- No physical Android device or real external model service has been tested.
- Browser/jsdom checks are configured for CI; their success must be confirmed in GitHub Actions, not assumed here.

## Limitations
This is **not** an APK and not a faithful recreation of proprietary Cursor functionality. This is a Cursor-inspired, original review/approval workflow. Free endpoints can have limits, privacy caveats and provider-side changes. The current app remains a prototype until tested on the phone and with an operational model provider.
