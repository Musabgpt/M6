# Decision records

## ADR-001 Not a verbatim copy of Cursor
Context: the request asked for an exact copy of Cursor in look, prompts, tools and extensions.
Decision: build an original app with a similar workflow (chat over the workspace, proposed diffs,
approval-gated commands, extensions). Do not copy Cursor's code, system prompt, branding or assets.
Reason: those belong to their owner. The workflow itself is not protected, so it can be rebuilt.
Reversal: none needed. The system prompt in `newal_mobile/prompt.py` was written from scratch.

## ADR-002 Python engine in Termux, served to the phone browser
Options considered:
- A. Extend the existing Kotlin Android app (app/ or android-lite/). Rejected for this round: no Android SDK
  or device is available here, so nothing could be built or run. The CI build also pulls external source.
- B. Python stdlib engine inside Termux with the browser UI (chosen). Termux already provides bash, git,
  and Python. The browser is the UI the user asked for. Verifiable here.
- C. Termux-only command-line tool. Rejected: no visual review of diffs, poor fit for the phone workflow.
Consequence: no APK yet. A thin WebView wrapper can be added later without changing the engine.

## ADR-003 Propose, then accept
Writes are stored as changes and applied only on explicit accept, with a conflict check on the file.
Reason: a model that edits silently is hard to audit on a phone screen. Diff review is the safe default.

## ADR-004 Approval per command, no always-allow
Each `run_command` shows the exact command and needs one tap. No standing permission exists.
Reason: model output and file content are untrusted. A standing grant would turn one injected instruction
into permanent shell access.

## ADR-005 Fallback stays within the privacy tier
A failing provider is replaced only by a provider in the same tier unless `allow_cross_tier` is set.
Reason: silently moving a private workspace to a public free route would violate protocol law L14 and A07.

## ADR-006 Stdlib only, no web framework
Reason: fewer supply-chain surfaces and nothing to install on Termux beyond Python.
Trade-off: no streaming yet. The server uses plain HTTP and returns whole turns.
