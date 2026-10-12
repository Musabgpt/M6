# Architecture

```
Phone browser (ui/index.html + ui/app.js)
        │  HTTP on 127.0.0.1:8787, bearer token, Host check, CSP
        ▼
newal_mobile/server.py   ── AppState (workspace, runs, lock)
        │
        ├── agent.py       Run: bounded tool-use loop, pauses for approval, loop/budget guards
        │       │
        │       ▼
        │   providers.py   OpenAI-compatible HTTP client + FallbackChain (privacy-tier aware)
        │       │
        │       ▼  HTTPS (or loopback for local models)
        │   model endpoint(s)
        │
        └── tools.py       ToolBox: list/read/search, propose_edit/propose_write,
                           run_command (approval gate), accept/reject with conflict check
                 │
                 ├── sandbox.py   path confinement (no absolute paths, no .., no symlink escape)
                 └── workspace on disk (~/workspace), Termux bash for commands
```

## Trust boundaries
1. Browser ↔ server: the token and the Host header. The page itself is public and contains no secrets.
2. Model output ↔ tools: every tool argument is validated; the model cannot write files directly.
3. Tool/file content ↔ instructions: tool results are passed as data. The approval gate holds even when
   the model obeys injected text (tested).
4. Workspace ↔ model provider: anything the agent reads leaves the phone. The sensitive-name guard limits this.

## Run lifecycle
`create` → `advance` (model turn, then tool calls) → either `done`, `failed`, or `awaiting_approval`.
`approval` resolves the paused call, records the result, and continues. Budgets: 12 steps, 300 s,
and at most 3 identical tool calls per run.

## Change lifecycle
`propose_*` stores a change (old and new text). `accept` re-reads the file and refuses if it no longer
matches the old text, then writes through a temp file and `os.replace`. `reject` discards it.
