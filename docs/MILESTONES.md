# Initial milestone plan

Milestones are sequential acceptance gates, not delivery-date promises. M0 is delivered here; all later milestones are planned.

| Milestone | Deliverables | Acceptance gate |
|---|---|---|
| M0 — Offline foundation | Domain scaffold, SQLite, fixture import, membership, private memory, paper-intent gate, tests and CI | Offline tests/demo pass; ZIP integrity and manifest verified |
| M1 — Trusted identity and persistence | Authentication adapter, immutable owner context, scoped repository boundary, versioned migrations, backup/restore | Cross-user reads/writes/prompts/caches denied; identities cannot be spoofed; restoration passes |
| M2 — Portfolio and public data | Authoritative instrument validation, aliases, import reconciliation, transactions/lots design, dated prices/NAV, baseline coverage, membership administration | Valid outside-universe holdings accepted; unsupported/ambiguous imports quarantined; 76th enhancement rejected; all holdings retain baseline coverage; financial fixtures reconcile |
| M3 — Proactive service | Durable jobs/leases, deterministic monitoring, user preferences, outbox, retry and deduplication | Crash/retry tests; no cross-user notifications; unchanged conditions stay quiet; stale data shown |
| M4 — Hybrid chat and local model | Authenticated chat over common services, model adapter, bounded context, evidence rendering, specialist tools | Offline/local run on target Mac; documented memory and latency benchmarks; prompt injection cannot grant access or actions |
| M5 — Experience learning | Scoped traces, extraction, review, versioned procedures, retrieval and ablation harness | Held-out improvement gate from MEMORY.md met; zero privacy regression; rollback demonstrated |
| M6 — Paper portfolio simulation | Separate ledger, cash/fees, buy/sell intents, reproducible stock/ETF fills and fund NAV handling | No real-holding mutation; deterministic ledger reconciliation; no live mode or brokerage adapter |

## First implementation slice

Implement M1 before any network interface or real user data. Select a local authentication mechanism, define immutable AuthContext, replace raw caller-controlled IDs at the application boundary, add owner-scoped repositories and migration version 1. Cover Alice/Bob access attempts, unknown identities, deleted ownership, restore and migration rollback. Preserve M0's offline demo and invariants.

## Backlog ordering within M2–M5

First establish instrument identity and reproducible accounting. Then reliable collection and minimal proactive rules. Add model explanations after deterministic outputs are trustworthy. Only then extract experiences and evaluate changed behavior. Keep specialist roles as modules until extra autonomous loops have measurable benefits.

## Definition of done

Code and contracts agree; tests exercise success, denial and retry/failure behavior; no private data or secrets in fixtures/logs; deterministic money math; provenance/freshness visible; local resource impact measured when relevant; documentation clearly distinguishes implemented behavior from planned work. CI must pass on supported macOS/Linux Python versions before release.
