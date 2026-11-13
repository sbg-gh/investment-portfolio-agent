# Security and local operations

M1 supplies authenticated local service contexts; Store remains a trusted-process persistence API. Any person with database or process access can read the SQLite file. Use OS account protections and encrypted local storage; multi-user remote exposure still requires transport/login and deployment hardening.

## Deployment progression

M0: offline in-memory demo or explicitly chosen local database. M1: authenticated local application services with scoped context and negative authorization tests; loopback transport arrives with the optional web UI (slice 7a, docs/UI.md). M3: one service supervising bounded job workers and an outbox. M4: optional local inference adapter with bounded resources. Bind loopback by default; external access requires an explicit transport/authentication design.

## Web UI server

`python -m portfolio_agent.web serve --db PATH` (ui extra required) binds `127.0.0.1:8765`. Non-loopback binds are refused unless `--allow-non-loopback`, `PORTFOLIO_AGENT_ALLOW_REMOTE=I-UNDERSTAND-THERE-IS-NO-TLS` and `--allowed-host` are all given; there is no TLS. Provision with `create-user` / `issue-credential` (token printed once, only its hash stored) and `revoke-credential`. `seed-starter-data --db PATH [--user USER_ID]` registers the 20 watchlist instruments as operator-attested (idempotent) and, with `--user`, creates that existing user's synthetic sample portfolio; it is trusted local administration, prints counts only, needs no ui extra, and never writes prices. Sessions live in memory, so a restart signs everyone out. All service calls run on one worker thread that owns the SQLite connection; a chat turn holds the transaction while the model runs, so a slow model blocks other requests until `--model-timeout`. The request log line has method, route template, status, duration and request id only. Build the frontend with `npm --prefix ui ci && npm --prefix ui run build`; without it the server serves a "UI not built" page.

Keep databases, models, logs, backups and credentials out of Git. Store future secrets in OS-protected configuration. Minimize logs to IDs, durations and error categories; private text requires explicit diagnostic consent and retention. Avoid embedding portfolio data into filenames or shared cache keys. Future private caches use owner scope in keys.

## SQLite lifecycle

The current application migrates to schema version 3 atomically and assumes one trusted application process. Use ordered migrations for future changes. Backup/restore and upgrade tests are included; see M1.md for legacy quarantine and recovery credential revocation. Keep databases on local disk. Configure and measure WAL and transaction behavior before concurrent workers; never share one Python SQLite connection across threads. The monitoring cap is enforced within SQLite, not only by an application count.

Use SQLite's backup API for a consistent live backup. Restore to a separate path, check integrity and schema version, and compare authorized holdings and memory counts. Protect backups to the same standard as the source. Implement user export/deletion across records, traces, vectors, caches and backups with a documented retention policy before real personal data rollout.

## Operational checks for later milestones

Expose queue depth, job age, stale/missing observation counts, inference latency, errors and monitoring occupancy without private payloads. Graceful shutdown stops claims and finishes or releases leases. Start-up resumes expired leases. Test provider rate limits, unavailable model runtime, partial import rollback, disk-full errors and notification retries. Baseline coverage degradation must be visible to the affected user.

Schema v3 observations and valuation snapshots are included in backups. See VALUATION.md. Current backup helpers require the current schema; upgrade an older archive on a separate copy before creating a new backup.
