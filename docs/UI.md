# Local web UI (slice 7a)

A browser UI for one machine: sign in with a provisioned token, manage portfolios and holdings, value them against operator-entered prices, and chat over your own recorded data. It is a local loopback listener, not a hosted multi-user service. Slice 7b (alerts, paper account, CSV ingest, membership/dead-job admin, tick-now, memory/procedure review) is planned and not implemented; see MILESTONES.md.

## Quick start

```sh
python3.11 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements/ui.lock && pip install -e '.[ui]'
npm --prefix ui ci && npm --prefix ui run build          # Node 18.20.2+, output is gitignored
python -m portfolio_agent.web create-user alice --db var/portfolio.db
python -m portfolio_agent.web issue-credential alice --db var/portfolio.db --role operator   # prints the token once
python -m portfolio_agent.web serve --db var/portfolio.db                                    # http://127.0.0.1:8765
```

Add `--model-from-env` with `MODEL_ENDPOINT` / `MODEL_NAME` (any OpenAI-compatible endpoint, e.g. `MODEL_ENDPOINT=http://localhost:11434 MODEL_NAME=llama3.2:3b` for Ollama) to use a local model. Without it, chat answers with deterministic facts and says the model is unavailable. If the UI was not built, the server still starts and serves a plain "UI not built" page; the API is unaffected. `revoke-credential CREDENTIAL_ID --db ...` revokes a credential and ends its sessions on the next request.

## Architecture

- `portfolio_agent.web` is an optional subpackage. Core, `cli` and all nine eval suites import nothing from it, and only `web/server.py` imports uvicorn (lazily, in `serve`). The `ui` extra pins `uvicorn==0.54.0`, `h11==0.16.0`, `click==8.5.1`; `requirements/ui.lock` carries hashes.
- uvicorn/h11 is the transport only. Routing, body/JSON handling, Host/Origin/CSRF checks, sessions, rate limits, static serving and SSE are our own framework-neutral code (`web/app.py`, `policy.py`, `wire.py`, `sessions.py`, `ratelimit.py`, `asgi.py`) and are tested in-process without the extra.
- All service calls run on ONE worker thread that owns the SQLite `Store` (sqlite connections have thread affinity). The queue is bounded (16 pending); a full queue answers 503 `busy`.
- Frontend: React 19 + TypeScript + Vite in `ui/`, hash router (portfolio ids in routes never reach the server or any log), no UI kit, no chart library. Allocation bars are hand-rolled SVG from BigInt basis-point math with a table twin. Money is decimal strings end to end; the client rejects numeric money fields.

## Threat model and controls

Trusted: the OS account running the server, the database file, the operator CLI. Untrusted: anything arriving over HTTP, other local processes and browser tabs, web pages the user visits (DNS rebinding, CSRF), and model output.

- Identity comes only from the session cookie, which maps to a credential id; every request re-authenticates that credential (`Authenticator.reauthenticate`), so revocation and expiry apply immediately. Client-supplied `user_id`/`owner`/`role`/`operator` fields are ignored.
- Sessions are in memory: 256-bit random id stored as a SHA-256 digest, 30 min idle, 8 h absolute, capped by credential expiry; login always mints a new id. A restart logs everyone out. Cookie `pa_session`: `HttpOnly; SameSite=Strict; Path=/; Max-Age=28800`, plus `Secure` only when the request came over HTTPS (Safari rejects Secure cookies on plain-HTTP localhost).
- Unsafe methods need an allowed `Origin` (required, not optional), `Content-Type: application/json` and the per-session `X-CSRF-Token`. The `Host` header must be in the allow-list (`127.0.0.1:PORT`, `localhost:PORT`, `[::1]:PORT` plus explicit `--allowed-host`), which defeats DNS rebinding. No CORS headers are ever sent.
- Every response: `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Resource-Policy: same-origin`; `/api` responses add `Cache-Control: no-store`.
- Errors are fixed codes: 401 `unauthenticated`, 403 `forbidden` (operator-only routes), one identical 404 body for missing and foreign resources, 422 fixed codes (never exception text), 413, 429 with `Retry-After`, 500 `{"error":"internal","request_id":…}`.
- Limits: body 64 KiB, path 512, query 2048, 64 headers / 8 KiB each / 32 KiB total, decimal strings up to 40 chars and JSON numbers rejected for money and quantities, chat message 2000 chars, 60-request burst / 30 per second per session and per peer, login 5 failures per peer and 50 global per 5 minutes, uvicorn concurrency 64.
- Logging: one line per request, `method route-template status duration request-id` (template like `/api/portfolios/{id}/holdings`); never concrete paths, query strings, headers, cookies, bodies, tokens or ids. uvicorn access log and `Server` header are off; proxy headers are not trusted.
- Model output is untrusted. Chat uses the unchanged M4 gate: only read-only tools, re-authorized per call; the frontend renders all model and evidence text as text nodes (`dangerouslySetInnerHTML` and `innerHTML` are lint-banned).

## Binding

`serve` binds loopback (`127.0.0.1`, `::1`; `localhost` means `127.0.0.1`) only. Any other address is refused unless all three are given: `--allow-non-loopback`, environment `PORTFOLIO_AGENT_ALLOW_REMOTE=I-UNDERSTAND-THERE-IS-NO-TLS`, and at least one `--allowed-host`. There is no TLS; anyone who can reach that address can try to log in, and tokens and cookies cross the network in clear text. A refused bind does not open or create the database.

## Endpoints

| Method | Path | Notes |
|---|---|---|
| POST | `/api/session` | login with `{"token"}`; throttled |
| GET / DELETE | `/api/session` | current session (role, CSRF token) / logout |
| GET / POST | `/api/portfolios` | list mine / create |
| GET / POST | `/api/portfolios/{id}/holdings` | list / import `{symbol, quantity, idempotency_key}` |
| DELETE | `/api/portfolios/{id}/holdings/{security_id}` | remove (with idempotency key) |
| GET | `/api/portfolios/{id}/receipts` | import/remove receipts |
| GET | `/api/portfolios/{id}/valuation?source=&as_of=` | total is `null` unless every line is fresh |
| GET | `/api/securities`, `/api/sources` | public instrument identity / known observation sources |
| POST | `/api/chat` | `text/event-stream`: `status`, keep-alive comments, `evidence`, `explanation`, `proposals`, `done` (`model_status`) |
| POST | `/api/operator/instruments`, `/api/operator/observations` | operator only |

## Known limits

- Single-worker serialization: chat holds the service transaction (`BEGIN IMMEDIATE`) while the model runs, so a slow model blocks every other request, including logout, until it answers or `--model-timeout` (default 30 s) expires.
- Sessions are in memory and process-local; restart signs everyone out.
- Instruments are operator-attested: nothing checks them against a market-data authority. There is no live market data; prices and NAVs are operator-entered.
- Chat is staged SSE, not token streaming: the M4 gate must parse the complete model reply before anything is used.
- Real local model quality: see "Real model smoke test" below. A 3B local model is unreliable at the structured-output format and its arithmetic can disagree with the deterministic evidence. The UI therefore always shows the deterministic evidence table and treats the model text as an unverified explanation. When the model is unreachable the server answers with deterministic facts (`model_status: unavailable`); when its output is not parseable the M4 gate returns the refusal text (status `ok`), not model content.
- Rate limiting is per peer only (no per-session bucket). On loopback every client shares the peer `127.0.0.1`, so the login throttle is effectively global and a local process can lock out login for 300 s. Static GETs bypass the rate limiter (the Host check still applies).
- Queue exhaustion: one authenticated user can fill the 16-slot worker queue with chat turns, pushing others to 503 `busy`; a client disconnect does not cancel a running turn. `Worker.submit` and `Worker.close` have no timeout beyond the model and SQLite timeouts. Per-session concurrency caps are follow-up work.
- Chat failures in `on_model_error='facts'` mode surface only as `model_status: unavailable`; the cause is not logged.
- `--allowed-host` extends the Host/Origin allow-list even on a loopback bind. No per-request body read timeout beyond uvicorn defaults. `GET .../valuation` writes a snapshot row.
- The API client's numeric-money guard uses a fixed `MONEY_KEYS` list (`quantity`, `value`, `total`, `priced_subtotal`, `market_value`, `unit_value`); a new money field added to the server must be added there too.
- Idempotency: the Holdings screen keeps one key per action until it succeeds, so retrying the same import or removal after a lost response reuses it. Switching to a different symbol, quantity or holding starts a new key.
- Prices are operator-entered; there is no live market-data adapter. Sessions are in memory, so a restart logs everyone out.
- CI: the `ui` job installs from the hashed lock, runs Node 18 lint/test/build, `npm audit --omit=dev`, the Python suite and `cli eval`. It has no secrets and makes no third-party calls in tests; actions are pinned to commit SHAs. The browser e2e and any real-model run are local only and are not in CI.
- Accessibility: the screens use landmarks, labels, a skip link, focus management, a polite live region, text-plus-icon status, a contrast-tested palette and jest-axe checks. WCAG conformance cannot be claimed without manual assistive-technology testing and expert review, which has not been done.

## Node 18 and dev-only advisories

The frontend stays on Node 18.20.2 (a user decision). The newest vitest, vite, jsdom and Playwright need Node 20/22. On 2026-11-12 in the worktree, `npm --prefix ui audit --omit=dev` reported `found 0 vulnerabilities`; the shipped bundle (react, react-dom) is unaffected. The full `npm --prefix ui audit` reported 14 advisories (7 moderate, 5 high, 2 critical), all in build-time tooling: `vitest`/`tinypool`/`@vitest/mocker` (test runner) and the eslint chain (`eslint`, `@eslint/config-array`, `@eslint/eslintrc`, `typescript-eslint` packages, `minimatch`, `brace-expansion`). These only process this repository's own code during tests and lint and are never served. The fixes need Node 20+; moving to Node 22 LTS is the follow-up.

## Testing

- Python HTTP-layer tests run in-process through the framework-neutral core with no extra; real-loopback server tests in `tests/test_web_server.py` skip with a visible reason when uvicorn/h11 are absent. The ninth eval suite `m7-web-security-baseline-v1` runs in `cli eval` with no extra.
- Frontend: `npm --prefix ui run lint`, `npm --prefix ui test` (Vitest + Testing Library + jest-axe), `npm --prefix ui run build`.
- Browser smoke: `E2E_PYTHON=/path/to/venv-with-ui-extra/bin/python npm --prefix ui run e2e` (after `npm --prefix ui run build`). `ui/e2e/serve_for_e2e.py` starts the real server on an ephemeral loopback port with a temp database, synthetic `e2e-user`/`e2e-operator` tokens and a fixed offline reply (no Ollama). `ui/e2e/smoke.mjs` drives the installed Google Chrome through `playwright-core` 1.54.2: operator login, register two instruments, record a fresh and a ten-day-old price, create a portfolio, import a holding, see the total (3 × 10 = 30), import the stale holding and see "Total withheld: 0 missing, 1 stale" with no total, chat and see the evidence table, sign out, press Back and confirm no private data and a 401 from the API. Exit 2 means it could not run (no Chrome, UI not built, server failed) and is never reported as a pass.
- Recorded run, 2026-11-12, macOS, Node 18.20.2, Chrome 157.0.8103.51 headless: all 8 steps passed.

## Real model smoke test (recorded exactly as run, 2026-11-12)

Ollama was running locally with `llama3.2:3b`. Commands (synthetic user, temp database, port 8791, ui-extra venv in `/tmp`):

```sh
python -m portfolio_agent.web create-user real-synthetic --db /tmp/pa-real/real.db
python -m portfolio_agent.web issue-credential real-synthetic --db /tmp/pa-real/real.db --role operator --ttl-hours 1
MODEL_ENDPOINT=http://localhost:11434 MODEL_NAME=llama3.2:3b \
  python -m portfolio_agent.web serve --db /tmp/pa-real/real.db --port 8791 --model-from-env --model-timeout 120
```

A small Python `http.client` script then did, over HTTP with Host/Origin/CSRF headers: login (200), register `op:acme` (200), record price 10 five minutes old (200), create a portfolio (201), import ACME × 3 (200), valuation (200, `complete`, total `30`), and `POST /api/chat` "What is my portfolio worth?".

- Before calibration (M4 prompt only): 1 of 1 chat turns returned the refusal text, because the model answered in free text, not the JSON the gate parses. Evidence (`op:acme`, fresh, `30`) was still returned and `model_status` was `ok`.
- After adding the opt-in `prompt_preamble` (only when a real model is configured; it asks for exactly one JSON object with an empty `tool_calls` list) and restarting the server: of 4 chat turns over HTTP, the first returned the refusal text and the next 3 returned a model explanation. A direct in-process probe of the same backend (6 turns, same database copy) parsed all 6, but the content was unreliable: several answers said the portfolio was worth $90 (it is $30; the evidence says `market_value=30`), one said the value was "not specified", and one claimed the valuation was incomplete (it was `complete`).
- Conclusion: the real round trip works end to end over HTTP and SSE, and the gate keeps unparseable replies harmless, but llama3.2:3b explanations are not trustworthy figures. The UI shows the deterministic evidence table beside every answer. No adaptation or quality claim follows from this run. The browser was not used for the real-model run; the Chrome e2e above uses the offline scripted reply.
