# Threat model and controls

## Assets
Workspace source code, the workspace's secrets (`.env`, keys), the access token, provider API keys,
and the phone itself (commands run as the Termux user).

## Attackers and entry points
| Attacker | Entry point | Control | Test |
|---|---|---|---|
| Other app on the phone | 127.0.0.1:8787 | Bearer token (compare_digest), token file mode 0600 | test_api_requires_a_valid_token |
| Malicious web page (DNS rebinding) | Host header | Only 127.0.0.1 / localhost accepted | test_foreign_host_header_is_refused |
| Malicious web page (CSRF) | Browser requests | Token sent in a header, not a cookie; no CORS; CSP `default-src 'none'` | by design, CSP header checked |
| Injected text in files or command output | Model context | Tool results are data; commands need approval | test_obeyed_prompt_injection_still_waits_for_approval |
| Model proposes a bad path | Path arguments | Realpath sandbox, no absolute paths | sandbox and symlink tests |
| Model reads secrets and sends them out | read_file, search | Sensitive-name guard (.env, keys, credentials) | SensitiveFileTests |
| Model edits a file behind the user's back | Write tools | Propose-only, accept re-checks the file | conflict and reject tests |
| Model runs destructive commands | run_command | Approval per command; best-effort blocklist | approval tests; blocklist test |
| Runaway model loops | Agent loop | Step, time, and repeat budgets | loop tests |
| Provider silently downgrades privacy | FallbackChain | Same-tier fallback only | test_does_not_silently_cross_privacy_tiers |
| API key leaks | Errors, logs, repr | Errors never include the key; repr hides it | test_error_messages_never_contain_the_api_key |

## Known residual risks (not mitigated in v0.1)
- **Approval is the real boundary, and it is only as good as the user reading the command.** A user who taps
  Allow on `curl ... | sh` has granted that. The blocklist is trivially bypassed and is documented as such.
- **Commands can read secrets** the file guard does not cover (for example `cat .env` inside a command, or
  `env`). Their output goes to the model. Treat commands on untrusted repositories with suspicion.
- **Free routes may keep prompts.** The UI shows a warning. Data handling of the default route is UNVERIFIED.
- **The token lives in browser localStorage.** Anything that runs script on this page could read it. Mitigated
  by CSP (`script-src 'self'`) and by `textContent` everywhere in the UI, but not eliminated.
- **Single-user design.** A global lock is held during model calls, so other requests wait.
- **The loopback port is reachable by other apps on the phone** that know the token. The token is the only barrier.
- **Accepted changes have no undo** in v0.1. Use git in the workspace.

## Supply chain
No third-party Python packages. The UI loads only its own `app.js` (no CDN). The browser test uses jsdom and is
not shipped to the phone. Dependencies in `tests/e2e/package-lock.json` are for development only.
