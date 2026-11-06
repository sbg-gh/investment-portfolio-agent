# Initial milestone plan

Milestones are sequential acceptance gates, not delivery-date promises. M0 is delivered. M1 local service/authentication, migrations, backup/restore and early traces/evals are implemented. Network transport, interactive login and prompt/cache integration remain deferred; M2a fixture-backed valuation is implemented; M2 is now complete (import provenance, the transaction ledger with reconciliation, deterministic baseline coverage and the enhanced-monitoring admission machine); M3–M6 are planned.

| Milestone | Deliverables | Acceptance gate |
|---|---|---|
| M0 — Offline foundation | Domain scaffold, SQLite, fixture import, membership, private memory, paper-intent gate, tests and CI | Offline tests/demo pass; ZIP integrity and manifest verified |
| M1 — Trusted identity and persistence | Authentication adapter, immutable owner context, scoped repository boundary, versioned migrations, backup/restore | Cross-user reads/writes/prompts/caches denied; identities cannot be spoofed; restoration passes |
| M2 — Portfolio and public data (gate met) | Authoritative instrument validation, aliases, import reconciliation, transaction ledger reconciling to the holdings snapshot, dated prices/NAV, deterministic baseline coverage, operator-gated membership admission | Met: valid outside-universe holdings accepted; unsupported/ambiguous imports quarantined; 76th enhancement rejected (queued, not active); all holdings retain baseline coverage; financial fixtures reconcile — all exercised by the `m2-completion-baseline-v1` eval suite |
| M3 — Proactive service | Durable jobs/leases, deterministic monitoring, user preferences, outbox, retry and deduplication | Crash/retry tests; no cross-user notifications; unchanged conditions stay quiet; stale data shown |
| M4 — Hybrid chat and local model | Authenticated chat over common services, model adapter, bounded context, evidence rendering, specialist tools | Offline/local run on target Mac; documented memory and latency benchmarks; prompt injection cannot grant access or actions |
| M5 — Experience learning | Scoped traces, extraction, review, versioned procedures, retrieval and ablation harness | Held-out improvement gate from MEMORY.md met; zero privacy regression; rollback demonstrated |
| M6 — Paper portfolio simulation | Separate ledger, cash/fees, buy/sell intents, reproducible stock/ETF fills and fund NAV handling | No real-holding mutation; deterministic ledger reconciliation; no live mode or brokerage adapter |

## Next implementation slice

M2 is complete. On top of M2a valuation and the import-provenance slice, the close-out adds schema v5 (three new tables: `transactions`, `monitoring_admissions`, `baseline_coverage`) under the same atomic upgrade / FK-check-before-commit / future-version-reject / backup-restore-and-rollback path as v4. The signed buy/sell transaction ledger reconciles against the holdings snapshot per `(portfolio_id, security_id)` without ever auto-mutating holdings; deterministic baseline coverage keeps coverage-set == held-set independent of the monitoring tier; and the operator-gated admission machine admits the first 75 distinct securities, queues the next up to a bounded queue, then rejects, with no auto-eviction. The five M2 acceptance behaviors are exercised by the `m2-completion-baseline-v1` eval suite wired into `cli eval`. Tax lots, cost basis, cash and fees stay deferred to M6; durable jobs/leases/outbox/retry stay M3.

Next is M3 — proactive service: durable jobs with leases, an outbox with deduplication, retry and crash/resume semantics, deterministic monitoring and per-user preferences. M2 answers "is every held security covered?"; M3 owns the durable collection/notification machinery that acts on that coverage. Before adding any network adapter, add authentication transport, rate limiting, prompt/cache isolation and deployment tests. Local services alone do not complete those integration gates.

Keep execution traces and deterministic evaluation fixtures active from M1 onward. Expand them with tool/evidence lineage as features arrive; automatic extraction and promotion remain M5.

## Backlog ordering within M2–M5

First establish instrument identity and reproducible accounting. Then reliable collection and minimal proactive rules. Add model explanations after deterministic outputs are trustworthy. Only then extract experiences and evaluate changed behavior. Keep specialist roles as modules until extra autonomous loops have measurable benefits.

## Definition of done

Code and contracts agree; tests exercise success, denial and retry/failure behavior; no private data or secrets in fixtures/logs; deterministic money math; provenance/freshness visible; local resource impact measured when relevant; documentation clearly distinguishes implemented behavior from planned work. CI must pass on supported macOS/Linux Python versions before release.
