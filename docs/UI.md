# Local web UI (slice 7a)

A browser UI for one machine: sign in with a provisioned token, manage portfolios and holdings, value them against operator-entered prices, and chat over your own recorded data. It is a local loopback listener, not a hosted multi-user service. Slice 7b (alerts, paper account, membership/dead-job admin, tick-now, memory/procedure review) is planned and not implemented; see MILESTONES.md.

## Quick start

```sh
python3.11 -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements/ui.lock && pip install -e '.[ui]'
npm --prefix ui ci && npm --prefix ui run build          # Node 18.20.2+, output is gitignored
python -m portfolio_agent.web create-user alice --db var/portfolio.db
python -m portfolio_agent.web issue-credential alice --db var/portfolio.db --role operator   # prints the token once
python -m portfolio_agent.web seed-starter-data --db var/portfolio.db --user alice            # optional starter data, see below
python -m portfolio_agent.web serve --db var/portfolio.db                                    # http://127.0.0.1:8765
```

Add `--model-from-env` with `MODEL_ENDPOINT` / `MODEL_NAME` (any OpenAI-compatible endpoint, e.g. `MODEL_ENDPOINT=http://localhost:11434 MODEL_NAME=llama3.2:3b` for Ollama) to use a local model. Without it, chat answers with deterministic facts and says the model is unavailable. If the UI was not built, the server still starts and serves a plain "UI not built" page; the API is unaffected. `revoke-credential CREDENTIAL_ID --db ...` revokes a credential and ends its sessions on the next request.

## Starter data (instruments and one sample portfolio)

A new database has no instruments, so every symbol is refused ("not registered") until an operator registers it. The starter seed registers the 20 symbols of the `ai-conviction` watchlist (MU, AVGO, SMCI, TSM, MRVL, NVDA, AMD, OKLO, SMR, LEU, SNOW, CRWV, ANET, ORCL, VRT, CCJ, GEV, BE, UEC, SKHY) and can create one sample portfolio. Two ways, same effect:

- Command (trusted local administration, like `create-user`; it works without the ui extra): `python -m portfolio_agent.web seed-starter-data --db PATH [--user USER_ID]`. It always registers the instruments (idempotent). With `--user` it also creates that existing user's sample portfolio. There is no authentication context on the command line, so `--user` is taken as given; an unknown user fails with the generic `error: unknown user or credential`. Output is one JSON line of counts only.
- Buttons: the Operator screen has "Register starter instruments" (operator role only). The Portfolios screen has "Create sample portfolio" (any signed-in user; it hides once a portfolio with that name exists). A single operator-role credential can press both, instruments first. If the instruments are missing, the second button says so, and links to the Operator screen only for an operator.

What is seeded and what is not:

- Instruments: kind `stock`, country `US`, id `op:<lowercase symbol>`, source `operator-attested:ai-conviction`. These are operator-ATTESTED, not validated against any market-data authority. SKHY is recorded with source `operator-attested:ai-conviction:user-attested`: it is the user's own statement that SKHY is a US-listed stock, and nothing verified it. The watchlist file's `pending` validation statuses are unchanged.
- Sample portfolio: named `Sample portfolio (synthetic)`, 20 holdings at the round quantity `100`, imported through the normal authorized import path, so each holding has a receipt with source `starter-sample-synthetic` and idempotency key `starter-sample-v1:<SYMBOL>`. It is synthetic and is not anyone's real positions. The sample is the caller's portfolio with that exact name that also has those receipts; a portfolio you named the same yourself is never adopted. Re-running creates no duplicate instruments, portfolios, holdings or receipts, and a holding you removed is not put back.
- No prices. Nothing writes market observations, so the sample's valuation shows every line as missing and no total. This is deliberate: AGENTS.md forbids invented prices and a made-up price for a real ticker would mislead. Enter prices on the Operator screen (Record observation) to see a total.
- The registration route ignores the request body and the sample route ignores any body `user_id`; the owner is always the session user.

If a Holdings import fails because the symbol is not registered, an operator sees a link to the Operator screen with the symbol pre-filled in the registration form (`#/operator?symbol=AMD`, only for symbols of valid shape), and a non-operator is told an operator must register the instrument. Neither message shows server text.

## Importing prices from a CSV
There is no live market-data feed and no licensed provider adapter was adopted (DECISIONS.md explains why). Prices come from a CSV you supply (or the manual Yahoo fetch below), for example a brokerage export. The app does not verify your data or its license. You are responsible for having the right to use and store what you import.
Format (UTF-8, a BOM is tolerated, CRLF or LF, quoted fields allowed). Header-only template (the Operator screen has a "Copy template" button; no real tickers or prices ship anywhere):
```
symbol,date,close
```
- Required columns `symbol`, `date`, `close`: exact names, case-insensitive, any order, each exactly once. Other columns are ignored: only their names are reported (lowercased, at most 32 characters, at most 20 names) and their values are never stored.
- `symbol` is trimmed and upper-cased and must already be registered (see Starter data). Unregistered symbols are rejected, never auto-registered. Mutual funds are rejected (`unsupported_kind`): NAV import is not supported.
- `date` is `YYYY-MM-DD` only and means the 16:00 America/New_York close of that session date, stored as UTC (21:00Z in winter, 20:00Z in summer). There is no holiday calendar: weekend and holiday dates are accepted as given, and early-close days are still treated as 16:00. A date whose 16:00 close is later than now is `future_date`.
- `close` is the raw close for that session as a plain decimal such as `123.45`. Thousands separators, `$`, exponents, signs, NaN and infinity are `invalid_format`; zero and values over 12 fractional digits, 28 digits or the magnitude limit are `invalid_value`. Adjusted closes are not distinguished: import raw closes.
- Limits: 1 MiB and 10,000 data rows per file. The web route also inherits the 65536-byte request body cap, which is about 2,000 rows (the screen refuses files over 60 KB); use the `import-prices` command (OPERATIONS.md) for larger files.
Flow on the Operator screen ("Import prices from CSV"): enter a price list name (the observation source, 1 to 64 characters from letters, digits, `.`, `_`, `-`, starting with a letter or digit), choose the file, press Validate (a dry run that writes no observations), read the per-row table, then press Import. Import stays disabled until the identical name and file text were validated; the server re-validates anyway. Valid rows are imported in one transaction; rejected rows are listed and never block them.
Result codes per row: `imported` (Added), `unchanged` (Already present: the latest value for that security, source and session already equals it, so re-importing a file changes nothing), `corrected` (the value differs from the latest one; written as a new observation with the import time as `fetched_at`, the old one is kept, and the row is marked with a text marker), `conflict` (not written; only possible with an identical injected clock), `unknown_symbol`, `invalid_value`, `invalid_date`, `future_date`, `duplicate_row` (every occurrence of a repeated symbol and date is rejected), `unsupported_kind`, `invalid_format`. File-level errors return 422 `{"error": code}` with `invalid_source`, `invalid_encoding`, `file_too_large`, `too_many_rows`, `empty_file` (including a header-only file), `invalid_header`, `invalid_csv` or `timezone_unavailable`. Results contain only line numbers, symbols and codes, never file text, values or server messages, and the request log line is unchanged.
After a successful import the source appears in `/api/sources`, so the Valuation screen offers it on first use.
## Fetching prices from Yahoo (unofficial)
The Operator screen has "Fetch prices from Yahoo (unofficial)" and the command `python -m portfolio_agent.web fetch-prices --db PATH [--dry-run]` does the same without the ui extra. It reads end-of-day closes for every registered stock and ETF (mutual funds are never requested) from Yahoo Finance's unofficial chart endpoint and stores them through the CSV importer's own pipeline with the source `yahoo-chart`. It is manual only: no schedule, no fetch on page load or start-up, no timers. The decision, the terms issue and the operator's informed acceptance are recorded in DECISIONS.md ("Yahoo Finance end-of-day fetch"). The CSV import remains the path for data you are licensed to use.
- What leaves the machine: one HTTPS GET per symbol to `query1.finance.yahoo.com` for `/v8/finance/chart/{SYMBOL}?range=5d&interval=1d`, with the header `User-Agent: portfolio-agent/<version> personal local-first app`. Nothing else: no cookies, crumb, token, credential or portfolio data. The symbol must be a registered stock or ETF and is URL-encoded. TLS certificate verification is on, redirects are never followed, requests have connect and read timeouts, the response is capped at 1 MiB and must be JSON.
- Pacing and limits: at least 1.5 seconds between requests, at most 40 requests per press (retries count; the rest are `skipped_budget`), up to 2 retries with exponential backoff and jitter on timeouts, connection errors and 5xx only. A 401, 403, 429 or an HTML page stops the whole press (`blocked` or `rate_limited`, the rest `skipped_stopped`); nothing is retried and there is no attempt to get around the block. A press of about 20 symbols takes roughly 30 to 60 seconds.
- Data rules: `range=5d`, every completed daily bar is kept. The value is `quote.close` (split-adjusted, not dividend-adjusted), converted from the float's shortest decimal text and quantized to 4 decimal places half-even. Each bar maps to its session date in the exchange's time zone and then to 16:00 New York that day, exactly like the CSV importer. The bar of a session that is not finished yet is dropped (and on an early-close day the bar is not stored before 16:00 New York), so a stored price is always a completed close. Currency must be USD and the instrument type EQUITY or ETF. Any zero, negative, non-finite or out-of-range close, a mismatched symbol, duplicate session dates, a future date or inconsistent arrays make that symbol `invalid_response`; null closes (holidays, halts) are skipped, never filled.
- Per-symbol codes (fixed; provider text is never shown or stored): `imported`, `unchanged` (a repeat press adds nothing), `corrected` (new value for an existing date; the old row is kept), `not_found`, `unsupported_kind`, `rate_limited`, `blocked`, `provider_error`, `invalid_response`, `skipped_budget`, `skipped_stopped`. A failed symbol keeps its earlier observations (which age) and is never filled with a guess. Dry run makes the real requests and writes nothing; it counts as a press for the busy rule and the request cap.
- Execution: the press runs on its own background thread, not on the single store worker, so other requests (including logout and valuation) are served while it runs. The store worker is used for two short operator-only calls, reading the symbols and one ingest transaction; the starting credential is re-authenticated at both, so a credential revoked or demoted mid-fetch writes nothing. `POST /api/operator/prices/fetch` with `{"dry_run": bool}` answers 202 `{fetch_id}` (409 `busy` while another press runs, 503 `unavailable` while the server shuts down); `GET /api/operator/prices/fetch/{fetch_id}` returns `{state: running|done|failed|unknown, progress: {done, planned}, result, error}`. State is in memory, only the latest press is kept, and a restart drops it: an unknown, replaced or other user's id answers 200 `{"state":"unknown"}` and the screen shows "interrupted". On shutdown the press is stopped and nothing is ingested.
- After a real press, `/api/sources` lists `yahoo-chart`, so the Valuation screen offers it on first use. Prices are end-of-day closes: use a freshness window of 3 days or more over a weekend.
## Freshness window on the valuation screen
`GET .../valuation?source=&as_of=&max_age_days=N` accepts digits only, 1 to 30 (anything else is 422 `invalid_field`, a repeated parameter is 422 `invalid_query`). The price window becomes N days and the NAV window `max(N, 3)` days; without the parameter the default stays 24 h for prices and 72 h for NAV. The screen offers 1, 3, 5, 7 and 14 days (default 1), always sends the choice, and shows "Prices up to N days old" from the policy the server reports it used. The saved snapshot records that policy. The total is still withheld when any line is stale or missing. A Friday close is about 65 hours old on Monday morning, so use 3 days or more over a weekend.
## Manual smoke test (use your own data, or the synthetic file below)
Use a throwaway database and a free port other than the one a running server uses (the examples use 8799; pick another if it is taken):
```sh
python -m portfolio_agent.web create-user ops --db /tmp/csv-try.db
python -m portfolio_agent.web issue-credential ops --db /tmp/csv-try.db --role operator   # prints the token once
python -m portfolio_agent.web seed-starter-data --db /tmp/csv-try.db --user ops
python -m portfolio_agent.web serve --db /tmp/csv-try.db --port 8799
```
Create a file with the header `symbol,date,close` and a few rows for starter symbols with obviously fake values (for example `AMD,<a date two days ago>,1.00`), then: sign in, Operator, "Import prices from CSV", name it, choose the file, Validate, Import. Open Portfolios, the sample portfolio, Valuation, pick your price list name: with 1 day it shows the total withheld, with 3 days it shows lines as fresh, and a total appears only when all 20 holdings have a price inside the window. Delete `/tmp/csv-try.db` afterwards. Never keep made-up prices for real tickers in a real database.
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
- Limits: body 64 KiB, path 512, query 2048, 64 headers / 8 KiB each / 32 KiB total, decimal strings up to 40 chars and JSON numbers rejected for money and quantities, chat message 2000 chars, 60-request burst / 30 per second per peer (no per-session bucket), login 5 failures per peer and 50 global per 5 minutes, uvicorn concurrency 64.
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
| GET | `/api/portfolios/{id}/valuation?source=&as_of=&max_age_days=` | total is `null` unless every line is fresh; `max_age_days` 1 to 30 sets the price window (NAV `max(N,3)` days), default 24 h / 72 h |
| GET | `/api/securities`, `/api/sources` | public instrument identity / known observation sources |
| POST | `/api/chat` | `text/event-stream`: `status`, keep-alive comments, `evidence`, `explanation`, `proposals`, `done` (`model_status`) |
| POST | `/api/operator/instruments`, `/api/operator/observations` | operator only |
| POST | `/api/operator/starter/instruments` | operator only; registers the 20 starter symbols (idempotent); returns `{registered, already_present, symbols}` |
| POST | `/api/operator/prices/import` | operator only; body `{source, csv_text, dry_run}` (`dry_run` must be a boolean); 200 `{dry_run, source, total_rows, counts, ignored_columns, rows: [{line, symbol, outcome}]}`; 422 `{error}` with a file-level code; see "Importing prices from a CSV" |
| POST | `/api/operator/prices/fetch` | operator only; body `{dry_run}` (boolean); 202 `{fetch_id}`, 409 `busy`; see "Fetching prices from Yahoo (unofficial)" |
| GET | `/api/operator/prices/fetch/{fetch_id}` | operator only; `{state, progress, result, error}`; unknown, lost or foreign ids answer 200 `{"state":"unknown"}` |
| POST | `/api/starter/sample-portfolio` | any signed-in user; creates or returns the session user's sample (201 created, 200 existing); 409 `starter_instruments_missing` creates nothing |

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
- The API client's numeric-money guard uses a fixed `MONEY_KEYS` list (`quantity`, `value`, `total`, `priced_subtotal`, `market_value`, `unit_value`); a new money field added to the server must be added there too. Counts therefore must not use those names: the starter result uses `symbols`, not `total`, because a numeric `total` is rejected as numeric money.
- The Portfolios screen decides whether to show the sample button by the portfolio name alone, so a portfolio you named `Sample portfolio (synthetic)` yourself hides the button even though the server would have created a separate sample.
- Idempotency: the Holdings screen keeps one key per action until it succeeds, so retrying the same import or removal after a lost response reuses it. Switching to a different symbol, quantity or holding starts a new key.
- Prices are operator-entered, CSV-imported or fetched on demand from Yahoo's unofficial endpoint; there is no scheduled or licensed market-data adapter. CSV import has no holiday calendar (weekend and holiday dates are accepted as given, early closes treated as 16:00), imports raw closes only (no adjusted-close handling), does not support mutual-fund NAV, interprets dates as New York session dates, needs system time zone data for America/New_York (without it the import fails with `timezone_unavailable`; no `tzdata` dependency was added), and the web route is limited to the 65536-byte body (about 2,000 rows). Sessions are in memory, so a restart logs everyone out.
- CI: the `ui` job installs from the hashed lock, runs Node 18 lint/test/build, `npm audit --omit=dev`, the Python suite and `cli eval`. It has no secrets and makes no third-party calls in tests; actions are pinned to commit SHAs. The browser e2e and any real-model run are local only and are not in CI.
- Accessibility: the screens use landmarks, labels, a skip link, focus management, a polite live region, text-plus-icon status, a contrast-tested palette and jest-axe checks. WCAG conformance cannot be claimed without manual assistive-technology testing and expert review, which has not been done.

- Yahoo fetch: unofficial and undocumented, so it can break, change shape or be blocked at any time (a block stops the press and is reported, never worked around). Its use is outside Yahoo's terms and was accepted by the operator. Symbols and prices are representative and non-authoritative. Mutual-fund NAV is not fetched. There is no holiday calendar beyond what Yahoo's own bars imply. Fetch status is in memory (one press at a time, restart loses it). While the press waits on the network the store worker is free, but a press with a slow Yahoo can run for minutes (each request is bounded by 5 s connect, 10 s read and a 20 s total).
## Node 18 and dev-only advisories

The frontend stays on Node 18.20.2 (a user decision). The newest vitest, vite, jsdom and Playwright need Node 20/22. On 2026-11-12 in the worktree, `npm --prefix ui audit --omit=dev` reported `found 0 vulnerabilities`; the shipped bundle (react, react-dom) is unaffected. The full `npm --prefix ui audit` reported 14 advisories (7 moderate, 5 high, 2 critical), all in build-time tooling: `vitest`/`tinypool`/`@vitest/mocker` (test runner) and the eslint chain (`eslint`, `@eslint/config-array`, `@eslint/eslintrc`, `typescript-eslint` packages, `minimatch`, `brace-expansion`). These only process this repository's own code during tests and lint and are never served. The fixes need Node 20+; moving to Node 22 LTS is the follow-up.

## Testing

- Python HTTP-layer tests run in-process through the framework-neutral core with no extra; real-loopback server tests in `tests/test_web_server.py` skip with a visible reason when uvicorn/h11 are absent. The ninth eval suite `m7-web-security-baseline-v1` runs in `cli eval` with no extra.
- Frontend: `npm --prefix ui run lint`, `npm --prefix ui test` (Vitest + Testing Library + jest-axe), `npm --prefix ui run build`.
- Browser smoke: `E2E_PYTHON=/path/to/venv-with-ui-extra/bin/python npm --prefix ui run e2e` (after `npm --prefix ui run build`). `ui/e2e/serve_for_e2e.py` starts the real server on an ephemeral loopback port with a temp database, synthetic `e2e-user`/`e2e-operator` tokens and a fixed offline reply (no Ollama). `ui/e2e/smoke.mjs` drives the installed Google Chrome through `playwright-core` 1.54.2: operator login, register two instruments, record a fresh and a ten-day-old price, create a portfolio, import a holding, see the total (3 × 10 = 30), import the stale holding and see "Total withheld: 0 missing, 1 stale" with no total, chat and see the evidence table, sign out, press Back and confirm no private data and a 401 from the API. Exit 2 means it could not run (no Chrome, UI not built, server failed) and is never reported as a pass.
- Live Yahoo check, 2026-11-13 19:19Z (market open, Friday): one real press of 5 symbols (AMD, CRWV, NVDA, SKHY, TSM) from a throwaway database in /tmp through the real server on a free loopback port (not 8765) and the real HTTPS transport: 5 requests, all HTTP 200 JSON, no block or challenge; the response keys and arrays matched the recorded fixtures; the open session's bar was dropped and 4 completed bars (2026-11-09 to 2026-11-12) per symbol were stored as `yahoo-chart`; a 5-holding valuation of that source was complete and fresh under 1-day and 5-day windows. Not verified: behavior after the close, weekends, holidays, ETFs, repeat presses against the live endpoint, the other 15 starter symbols, any block. The user runs the full 20-symbol press.
- Yahoo fetch follow-up: the smoke test now has 13 steps (adds the Yahoo fetch button against `fake_yahoo` in `ui/e2e/serve_for_e2e.py`, a transport that never opens a socket, and the valuation of source `yahoo-chart` under a 5-day window, 5 × 123.4567 = 617.2835). The e2e never reaches Yahoo.
- Recorded run, 2026-11-12, macOS, Node 18.20.2, Chrome 157.0.8103.51 headless: all 8 steps passed.
- CSV import follow-up, 2026-11-13, same environment: the smoke test now has 11 steps (adds CSV validate with one unregistered symbol, CSV import, and valuation of the imported prices withheld under the 1-day window and complete under 3 days, 3 × 12.5 + 2 × 6 = 49.5); all 11 passed.
- Real server check, 2026-11-13 (throwaway database in /tmp, free loopback port, not the port of any other server; deleted afterwards): operator and non-operator users, `seed-starter-data`, curl with session cookie and CSRF header. A non-operator import was 403; a validate of a synthetic 7-row file (3 valid, 1 unknown symbol, 2 duplicate rows, 1 malformed close) wrote 0 observations; the import wrote 3; `/api/sources` listed the new source; valuation of that partial file was `incomplete` with no total with and without `max_age_days=30`; a synthetic file pricing all 20 starter symbols (1.00 to 20.00, two days old) was `stale` with no total by default and with `max_age_days=1`, and `complete` with total 21000 at `max_age_days=3`; `max_age_days=31` was 422 `invalid_field`. The `import-prices` command on the same database reported `corrected` 1 and `unchanged` 19 for a changed price (total then 21050), `unchanged` 20 on re-run, and exit 3 for a file with rejected rows; the server log had no symbol or source names.

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
- After adding the opt-in `prompt_preamble` (only when a real model is configured; it asks for exactly one JSON object with an empty `tool_calls` list) and restarting the server: of 4 chat turns over HTTP, the first returned the refusal text and the next 3 returned a model explanation. An additional in-process probe was run by hand and not recorded in the repo, so its counts are not claimed here; it was consistent with the above (some answers misstated the portfolio value or the valuation completeness against the deterministic evidence).
- Conclusion: the real round trip works end to end over HTTP and SSE, and the gate keeps unparseable replies harmless, but llama3.2:3b explanations are not trustworthy figures. The UI shows the deterministic evidence table beside every answer. No adaptation or quality claim follows from this run. The browser was not used for the real-model run; the Chrome e2e above uses the offline scripted reply.
