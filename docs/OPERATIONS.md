# Security and local operations

M0 is a trusted-process offline development foundation. Its explicit user IDs exercise repository scoping but are not authentication. Any person with database or process access can read the SQLite file. Use OS account protections and encrypted local storage; multi-user remote exposure requires M1.

## Deployment progression

M0: offline in-memory demo or explicitly chosen local database. M1: authenticated loopback service with scoped application context and negative authorization tests. M3: one service supervising bounded job workers and an outbox. M4: optional local inference adapter with bounded resources. Bind loopback by default; external access requires an explicit transport/authentication design.

Keep databases, models, logs, backups and credentials out of Git. Store future secrets in OS-protected configuration. Minimize logs to IDs, durations and error categories; private text requires explicit diagnostic consent and retention. Avoid embedding portfolio data into filenames or shared cache keys. Future private caches use owner scope in keys.

## SQLite lifecycle

M0 creates its initial schema on startup and assumes one trusted application process. Do not change its schema in place for a deployed dataset. Before M1 persistence release, add schema versions, ordered migrations, backups and restore tests. Keep databases on local disk. Configure and measure WAL and transaction behavior before concurrent workers; never share one Python SQLite connection across threads. The monitoring cap is enforced within SQLite, not only by an application count.

Use SQLite's backup API for a consistent live backup. Restore to a separate path, check integrity and schema version, and compare authorized holdings and memory counts. Protect backups to the same standard as the source. Implement user export/deletion across records, traces, vectors, caches and backups with a documented retention policy before real personal data rollout.

## Operational checks for later milestones

Expose queue depth, job age, stale/missing observation counts, inference latency, errors and monitoring occupancy without private payloads. Graceful shutdown stops claims and finishes or releases leases. Start-up resumes expired leases. Test provider rate limits, unavailable model runtime, partial import rollback, disk-full errors and notification retries. Baseline coverage degradation must be visible to the affected user.
