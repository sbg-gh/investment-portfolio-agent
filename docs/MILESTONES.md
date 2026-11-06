# Initial milestone plan

Milestones are sequential acceptance gates, not delivery-date promises. M0 is delivered. M1 local service/authentication, migrations, backup/restore and early traces/evals are implemented. Network transport, interactive login and prompt/cache integration remain deferred; M2a fixture-backed valuation is implemented; M2 is complete (import provenance, the transaction ledger with reconciliation, deterministic baseline coverage and the enhanced-monitoring admission machine); M3 is now complete (durable jobs/leases, a persisted outbox with deduplication, per-user preferences, deterministic monitoring and crash/resume semantics); M4–M6 are planned.

| Milestone | Deliverables | Acceptance gate |
|---|---|---|
| M0 — Offline foundation | Domain scaffold, SQLite, fixture import, membership, private memory, paper-intent gate, tests and CI | Offline tests/demo pass; ZIP integrity and manifest verified |
| M1 — Trusted identity and persistence | Authentication adapter, immutable owner context, scoped repository boundary, versioned migrations, backup/restore | Cross-user reads/writes/prompts/caches denied; identities cannot be spoofed; restoration passes |
| M2 — Portfolio and public data (gate met) | Authoritative instrument validation, aliases, import reconciliation, transaction ledger reconciling to the holdings snapshot, dated prices/NAV, deterministic baseline coverage, operator-gated membership admission | Met: valid outside-universe holdings accepted; unsupported/ambiguous imports quarantined; 76th enhancement rejected (queued, not active); all holdings retain baseline coverage; financial fixtures reconcile — all exercised by the `m2-completion-baseline-v1` eval suite |
| M3 — Proactive service (gate met) | Durable jobs/leases, deterministic monitoring, user preferences, outbox, retry and deduplication | Met: crash/retry resumes without duplicate effect; no cross-user notifications; unchanged conditions stay quiet; stale data shown (never fabricated) — all exercised by the `m3-proactive-baseline-v1` eval suite |
| M4 — Hybrid chat and local model | Authenticated chat over common services, model adapter, bounded context, evidence rendering, specialist tools | Offline/local run on target Mac; documented memory and latency benchmarks; prompt injection cannot grant access or actions |
| M5 — Experience learning | Scoped traces, extraction, review, versioned procedures, retrieval and ablation harness | Held-out improvement gate from MEMORY.md met; zero privacy regression; rollback demonstrated |
| M6 — Paper portfolio simulation | Separate ledger, cash/fees, buy/sell intents, reproducible stock/ETF fills and fund NAV handling | No real-holding mutation; deterministic ledger reconciliation; no live mode or brokerage adapter |

## Next implementation slice

M3 is complete. On top of the M2 coverage bookkeeping it adds schema v6 (four new tables: `jobs`, `notification_outbox`, `notification_preferences`, `monitoring_state`) under the same atomic upgrade / FK-check-before-commit / future-version-reject / backup-restore-and-rollback path as v4/v5. A deterministic, test-invoked `JobRunner` polls due jobs over `Store.transaction()` with leases (`LEASE_SECONDS=300`), bounded retries (`MAX_ATTEMPTS=3`), a bounded claim per tick (`MAX_CLAIM_PER_TICK=8`) and a `status='dead'` dead-letter; an expired-lease job resumes without duplicating its effect and ownership is re-authorized at execution time. The persisted outbox dedups on `(user_id, dedup_key)` (at-least-once, no double-send), per-user preferences gate quiet-hours/materiality/cooldown, and the two monitoring rules (stale-coverage + materiality crossing) read existing data only, stay quiet on unchanged inputs and never fabricate a value. Public collection coalesces by `(security, source, time-bucket)`, holder-blind, with private analysis kept owner-scoped. The four M3 acceptance behaviors plus reauthorize-at-execution and dedup are exercised by the `m3-proactive-baseline-v1` eval suite wired into `cli eval` (now four suites).

Next is M4 — hybrid chat and local model: authenticated chat over the common application services, a local model adapter with bounded context and concurrency, evidence rendering, and specialist tools. Model output is untrusted input; prompt injection must not grant access or actions. Before adding any network adapter, add authentication transport, rate limiting, prompt/cache isolation and deployment tests. Local services alone do not complete those integration gates.

Keep execution traces and deterministic evaluation fixtures active from M1 onward. Expand them with tool/evidence lineage as features arrive; automatic extraction and promotion remain M5.

## Backlog ordering within M2–M5

First establish instrument identity and reproducible accounting. Then reliable collection and minimal proactive rules. Add model explanations after deterministic outputs are trustworthy. Only then extract experiences and evaluate changed behavior. Keep specialist roles as modules until extra autonomous loops have measurable benefits.

## Definition of done

Code and contracts agree; tests exercise success, denial and retry/failure behavior; no private data or secrets in fixtures/logs; deterministic money math; provenance/freshness visible; local resource impact measured when relevant; documentation clearly distinguishes implemented behavior from planned work. CI must pass on supported macOS/Linux Python versions before release.
