# Initial milestone plan

Milestones are sequential acceptance gates, not delivery-date promises. M0 is delivered. M1 local service/authentication, migrations, backup/restore and early traces/evals are implemented. Network transport, interactive login and prompt/cache integration remain deferred; M2a fixture-backed valuation is implemented; the remaining M2 work and M3–M6 are planned.

| Milestone | Deliverables | Acceptance gate |
|---|---|---|
| M0 — Offline foundation | Domain scaffold, SQLite, fixture import, membership, private memory, paper-intent gate, tests and CI | Offline tests/demo pass; ZIP integrity and manifest verified |
| M1 — Trusted identity and persistence | Authentication adapter, immutable owner context, scoped repository boundary, versioned migrations, backup/restore | Cross-user reads/writes/prompts/caches denied; identities cannot be spoofed; restoration passes |
| M2 — Portfolio and public data | Authoritative instrument validation, aliases, import reconciliation, transactions/lots design, dated prices/NAV, baseline coverage, membership administration | Valid outside-universe holdings accepted; unsupported/ambiguous imports quarantined; 76th enhancement rejected; all holdings retain baseline coverage; financial fixtures reconcile |
| M3 — Proactive service | Durable jobs/leases, deterministic monitoring, user preferences, outbox, retry and deduplication | Crash/retry tests; no cross-user notifications; unchanged conditions stay quiet; stale data shown |
| M4 — Hybrid chat and local model | Authenticated chat over common services, model adapter, bounded context, evidence rendering, specialist tools | Offline/local run on target Mac; documented memory and latency benchmarks; prompt injection cannot grant access or actions |
| M5 — Experience learning | Scoped traces, extraction, review, versioned procedures, retrieval and ablation harness | Held-out improvement gate from MEMORY.md met; zero privacy regression; rollback demonstrated |
| M6 — Paper portfolio simulation | Separate ledger, cash/fees, buy/sell intents, reproducible stock/ETF fills and fund NAV handling | No real-holding mutation; deterministic ledger reconciliation; no live mode or brokerage adapter |

## Next implementation slice

M2a now supplies fixture-backed read-only valuation, dated observations, explicit missing/stale results and private reproducible snapshots. See VALUATION.md for limits and tests.

The import-provenance and reconciliation slice is now implemented (TDD): explicit audited holding removal (no silent overwrite), source/as-of metadata and import receipts, stable effective-dated aliases resolving as of a date, and idempotent import/retry keyed on a caller-supplied `(portfolio_id, idempotency_key)`. Schema is now v4 (three new tables: `import_receipts`, `holding_events`, `security_aliases`) with upgrade, rollback and backup/restore tests. The 400 target and 75-slot tier remain independent of valid holdings. Real provider integration follows only after coverage, licensing and publication/freshness rules are reviewed. Transactions/lots, baseline collection scheduling and admission queues remain incomplete M2 work.

Keep execution traces and deterministic evaluation fixtures active from M1 onward. Expand them with tool/evidence lineage as features arrive; automatic extraction and promotion remain M5. Before adding any network adapter, add authentication transport, rate limiting, prompt/cache isolation and deployment tests. Local M1 services alone do not complete those integration gates.

## Backlog ordering within M2–M5

First establish instrument identity and reproducible accounting. Then reliable collection and minimal proactive rules. Add model explanations after deterministic outputs are trustworthy. Only then extract experiences and evaluate changed behavior. Keep specialist roles as modules until extra autonomous loops have measurable benefits.

## Definition of done

Code and contracts agree; tests exercise success, denial and retry/failure behavior; no private data or secrets in fixtures/logs; deterministic money math; provenance/freshness visible; local resource impact measured when relevant; documentation clearly distinguishes implemented behavior from planned work. CI must pass on supported macOS/Linux Python versions before release.
