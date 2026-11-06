# Domain and security-universe specification

## Identity and ownership

User owns portfolios; portfolio owns positions and paper records. Every externally callable operation obtains the user from trusted authentication, then checks ownership. IDs alone never authorize access. Future repositories must scope SQL reads and writes, caches, events, prompts and notifications. Shared instrument metadata is public; portfolio membership is private.

M0 uses opaque UUID portfolio IDs and explicit trusted caller IDs. It implements a holdings snapshot, not a transaction ledger. The M0 `import_holding` path replaces a holding's quantity silently; M2 adds `import_holding_with_provenance`, which records source and market as-of provenance per import, writes an import receipt and a holding event, and is idempotent on a caller-supplied `(portfolio_id, idempotency_key)`. Removal is now explicit and audited through `remove_holding`: removing a holding that does not exist raises a reconciliation error rather than a silent no-op. The legacy `import_holding` remains for back-compat. Positive finite decimal strings preserve precision. Money and quantities must never use binary floats.

## Security master and membership

Stable internal security ID is the primary identity; symbols are display/resolution aliases, not permanent identities. M0's one unique symbol per security is a simplified fixture constraint. M2 now implements effective-dated symbol aliases: `resolve_alias(symbol, as_of)` maps a symbol to an already-registered security over a half-open window `[effective_from, effective_to)` (newest effective_from wins). Resolution runs before the authoritative validator and never overrides or invents authoritative identity; on a miss, import falls through to the validator's auto-registration. Exchange/provider identifiers, currency, status, eligibility evidence, validation timestamps and corporate-action reconciliation remain M2 work.

Three independent concepts:

1. Security master: all validated supported instruments; no 400/75 limit.
2. Research universe: curated membership aiming at 400 global instruments; explicit membership and reasons; can exceed target with review.
3. Enhanced monitoring: at most 75 distinct global instruments with higher-cost collection/analysis. Membership does not follow automatically from research or holdings.

Every validated holding auto-registers in the master, including instruments outside research or enhanced monitoring. Baseline coverage includes all held instruments. A full enhanced tier never rejects a valid holding. M0 enforces 75 with a SQLite trigger inside transactions and idempotent insert behavior. M2 adds the admission machine and deterministic baseline-coverage bookkeeping (both below).

### Enhanced-monitoring admission machine (M2 v5)

`propose_enhancement(security_id, *, reason, operator, now, queue_capacity=ADMISSION_QUEUE_CAPACITY)` is a deterministic admit → queue → reject state machine with no auto-eviction. The first `MONITORING_CAPACITY` (75) distinct securities are `admitted` into the active `monitoring` set (still the active-tier source of truth, backed by the untouched `monitoring_cap` trigger). The next distinct securities are `queued` FIFO (position = next index, `proposed_at` = UTC ISO-8601) up to `ADMISSION_QUEUE_CAPACITY` (default 25). Beyond that a proposal is `rejected` and nothing is written. Re-proposing an already admitted/queued security is idempotent — it returns the current state and adds no duplicate row (`monitoring_admissions.security_id` is unique). A queued security is NOT in the active tier; the 76th distinct enhancement is therefore rejected from the active tier even though it is recorded pending. `monitoring_admissions()` lists admitted first, then queued FIFO by position, tie-break on the stable `security_id`. A full tier (and full queue) never blocks a valid holding import — admission and holdings are independent. Reassessment cadence, expiry and automatic replacement/hysteresis remain future work; counts use integers only, no Decimal, and the audit never exposes which users caused prioritization.

### Deterministic baseline coverage (M2 v5)

Coverage is maintained atomically with holding import/removal, independent of the monitoring tier: a security held by ≥1 portfolio has `baseline_coverage.covered=1`. `import_holding`/`import_holding_with_provenance` upsert coverage (INSERT OR IGNORE, `since` = the import cutoff when available else a UTC now); `remove_holding` drops the coverage row once no portfolio still holds the security, so the covered-set equals the distinct-held-securities-set exactly. `reconcile_baseline_coverage()` asserts that equality and returns discrepancies (`held_without_coverage` or `coverage_without_holder`); an empty list means consistent. An outside-universe holding stays covered even with the enhanced tier and its queue full.

### M2/M3 boundary

M2 answers "is every held security covered?" — deterministic coverage bookkeeping and reconciliation over current state. M3 (now implemented) owns the durable machinery that acts on that coverage: jobs, leases, outbox, retry and crash/resume. See the proactive domain section below and ../M3.md for the implemented contract.

### Proactive domain and monitoring (M3 v6)

A durable job carries a trusted `owner_ref` (private work) or a `public_scope` (holder-blind public work), never a caller-asserted identity. A private `monitor_portfolio` job re-authorizes the CURRENT owner of its `owner_ref`/portfolio at execution time via `Store._authorize`: an ownership change after enqueue means the job never reads or notifies on the old owner's private data. Jobs are deterministic — a test-invoked `JobRunner` polls due jobs, leases them (`LEASE_SECONDS=300`), bounds retries (`MAX_ATTEMPTS=3`) and a claim per tick (`MAX_CLAIM_PER_TICK=8`), and routes an exhausted job to `status='dead'`; an expired lease makes the job immediately due again and a resumed, idempotent handler applies its effect exactly once.

Two monitoring rules read existing data only and never fabricate a value: stale-coverage (`fresh|stale|missing` via `FreshnessPolicy`, surfaced exactly as `value_portfolio`) and materiality crossing (Decimal change ratio of the two most recent observations vs the per-user `materiality_threshold`). A notification is enqueued only on a `monitoring_state` change, so unchanged inputs stay quiet; a deterministic dedup key `user|security|rule|state` plus the outbox `UNIQUE(user_id, dedup_key)` prevents a double-send. Public collection coalesces by `(security, source, time-bucket)` and reveals no holder identity; private per-user analysis and the owner-scoped outbox stay separate. Per-user `notification_preferences` (quiet hours, materiality threshold, cooldown, channel) are derived from the trusted context, never a caller-supplied id. All job/outbox/state rows are payload-free.

### Chat and local-model domain (M4, no schema change)

The local model is UNTRUSTED input and never owns access control, accounting, eligibility or authorization. It only PROPOSES an explanation and zero or more structured tool calls; deterministic code authorizes and executes. Specialist tools (`list_holdings`, `value_portfolio`) are READ-ONLY and carry no owner/user id argument — a proposed call is admitted only after schema validation against a fixed allow-list AND a fresh re-authorization of the current owner through `Store._authorize`, so a malformed, aliased or cross-user proposal executes nothing. Chat context is owner-scoped and bounded: it is authorized before any read, contains only the requesting owner's holdings and valuation, and is capped by an estimated token budget (`chars/4`, no tokenizer). No cross-user data ever enters another user's chat context. Evidence reuses M2a freshness (`fresh|stale|missing` with as-of times) and is never fabricated; missing data stays missing. Identity is derived only from the AuthContext — never from the chat message, the model, a tool result or the request body — and treated text (the user message, context data, tool results) is data, never executable instructions. M4 adds NO schema change: `conversation_id` is an opaque echoed correlation token, the response `request_id` reuses the payload-free `execution_traces.request_id`, and no conversation message is persisted.

## Transaction ledger (M2 v5) and future portfolio entities (M6)

M2 v5 implements a signed transaction ledger: `transactions` records buy/sell Decimal quantity events `(portfolio_id, security_id, side, quantity, source, idempotency_key, occurred_at)` with `UNIQUE(portfolio_id, idempotency_key)` enforcing external idempotency. `record_transaction` appends one signed event and never auto-mutates holdings; a retry under the same `(portfolio_id, idempotency_key)` returns the prior id without re-applying. `reconcile_portfolio` computes the signed Decimal sum per `(portfolio_id, security_id)` (buy +, sell −) and compares it to the holdings snapshot quantity, returning `(security_id, ledger_sum, holding_qty, reconciled)` rows; a security present only in the ledger yields `holding_qty=None` and `reconciled=False`. The ledger reconciles against the snapshot and flags divergence; it never coerces it.

Deferred to M6: accounts, cash balances, tax lots, cost basis, fees, corporate actions and reproducible fills. Each private record keeps an owner path, provenance and audit timestamp; snapshots record as-of time and reconciliation status. Keep original imports for authorized audit under retention policy.

Market observations carry security ID, value, currency, observation time, fetch time, source and price basis. US mutual funds use dated NAV; never describe stale NAV as a live quote or assume intraday execution. Missing prices remain missing rather than zero. Performance, fees, dividends and splits need explicit accounting conventions and fixtures before reporting returns.

## M0 schema mapping

`users`, `portfolios`, `holdings`: private ownership path. `securities`, `research`, `monitoring`: shared metadata. `memories`: explicit scope and owner constraints; public facts require public scope and NULL owner. Legacy ambiguous ownerless memories move to operator-only `memory_quarantine`. `paper_orders`: private recorded buy intent via portfolio ownership. See core.py for executable schema. Direct SQL and operator registration are trusted internal operations; future network interfaces must not expose the Store object wholesale.

M1 adds `credentials` and owner-scoped `execution_traces`; see ../M1.md for migration and access contracts.

M2a schema v3 adds shared market_observations and private valuation_snapshots. Snapshots embed current held quantities, market cutoff, source and evidence IDs. They are not historical position ledgers.

M2 schema v4 adds private `import_receipts` (one per applied import/removal, with source, market as-of, quantity, action and a `UNIQUE(portfolio_id, idempotency_key)` constraint enforcing idempotency), `holding_events` (an audit event per import/removal referencing its receipt), and shared `security_aliases` (effective-dated symbol-to-security mappings). `holdings` stays the current-state snapshot; audit history lives in the new tables.

M2 schema v5 adds three tables and leaves `holdings` and the `monitoring` table/`monitoring_cap` trigger unchanged: private `transactions` (signed buy/sell Decimal events with `UNIQUE(portfolio_id, idempotency_key)`, carrying external idempotency keys as the ledger note requires), shared `monitoring_admissions` (admit/queue audit rows, `UNIQUE(security_id)`, `position` NULL for admitted / FIFO index for queued), and shared `baseline_coverage` (`security_id` PK, `covered` 0/1, `since`).

M3 schema v6 adds four payload-free tables and leaves all v1–v5 tables and triggers unchanged: `jobs` (durable jobs with `status IN ('pending','leased','done','dead')`, an `owner_ref`/`public_scope` XOR check, `UNIQUE(type, idempotency_key)` and a `(status, due_at)` index), `notification_outbox` (`delivery_status IN ('pending','sent','suppressed')`, `UNIQUE(user_id, dedup_key)`, `(user_id, created_at)` index), `notification_preferences` (`user_id` PK with quiet hours, materiality threshold text, cooldown and channel), and `monitoring_state` (`PRIMARY KEY(security_id, rule)` with `state_key`/`observed_at`). A fresh database ends at v6. Each upgrade is atomic with a foreign-key check before commit; a future `user_version` is rejected; backup/restore carries the new tables forward and rollback leaves the prior version's data intact.
