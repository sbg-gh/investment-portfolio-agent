# Application and adapter contracts

## Internal persistence contract (M0 compatibility)

`Store.import_holding(user_id, portfolio_id, symbol, quantity, validator)` authorizes first, resolves through a validator, and atomically registers/upserts. FixtureValidator only resolves supplied synthetic records. A real validator must reject ambiguity, stale identity evidence and unsupported instrument types.

`holdings(user_id, portfolio_id)` returns scoped `(security_id, decimal-string quantity)` rows. `set_research` and `enable_monitoring` are trusted operator methods. `retrieve_memory(user_id)` returns that user's records plus public facts, rejecting unknown users. `record_paper_order(..., mode='read_only')` rejects unless mode is exactly `paper`. M0 has no brokerage port.

MarketData, LocalModel and Notifier protocols in ports.py define future integration seams, not implemented integrations. Provider adapters must enforce timeouts, retries with backoff, provenance, schema validation and data licensing constraints. Model adapters have explicit token bounds and a service-level concurrency limit. Treat retrieved documents as untrusted data, never executable instructions.

## M4 chat boundary (implemented)

`ChatService(service, model, *, config, clock=None).chat(context, *, conversation_id, message, portfolio_id=None)` is the authenticated, READ-ONLY chat contract. Identity comes ONLY from the AuthContext — no client-, chat- or model-supplied owner id is ever trusted as authority. The whole turn runs through `PortfolioService._run` with operation name `chat` and a PAYLOAD-FREE trace (no message body, holdings, symbols or sources). `portfolio_id`, when supplied, is authorized through the M1 path before any read; a cross-user or forged/expired context is denied.

The turn assembles a bounded, owner-scoped context (that owner's holdings plus an M2a valuation within `MODEL_CONTEXT_BUDGET`; exceeding the budget raises a bounded-context error), then asks the UNTRUSTED model to propose an explanation plus zero or more structured tool calls. Every proposed call passes the deterministic schema+authorization gate — `validate_proposal` against a fixed allow-list of READ-ONLY tools, then a fresh `Store._authorize` re-check — BEFORE execution; a rejected, malformed or cross-user proposal executes nothing and is dropped. The response is `{explanation, evidence, proposed_actions, conversation_id, request_id}`: `evidence` is deterministic references with `security_id`/`status`/`market_value`/`observed_at`/`as_of` reusing M2a stale/missing freshness (never fabricated); `proposed_actions` are validated, re-authorized, executed READ-ONLY calls only; `conversation_id` is the opaque echoed correlation token; `request_id` is the payload-free trace id. An unparseable or malformed model output yields a generic refusal and no actions.

The model adapters live in `model.py` (stdlib only): `OpenAICompatibleModel` (urllib `/v1/chat/completions`), `BoundedModel` (concurrency + output-token caps) and the injectable `ScriptedModel`, selected by `ModelConfig.from_env` (`MODEL_ENDPOINT`/`MODEL_NAME`/`MODEL_MAX_CONCURRENCY`/`MODEL_CONTEXT_BUDGET`/`MODEL_MAX_OUTPUT_TOKENS`). No schema was added: `conversation_id` is an opaque echoed token, `request_id` reuses `execution_traces.request_id`, and no conversation message is persisted. See ../M4.md for the full contract and THE CENTRAL SECURITY PROPERTY.

## M5a memory substrate (implemented; substrate only)

`PortfolioService.add_private_memory`-backed `add_memory` keeps the M1 3-arg form and accepts
the richer M5a contract (portfolio_id, payload, evidence_ids, expires_at, confidence,
applicable_conditions, source_run_id); a supplied `portfolio_id` is re-authorized and new rows
default `status='active'`, `version=1`, `review_status='unreviewed'`. `capture_run(context, *,
task_type, policy_version, tool_sequence, model_version=None, observation_timestamps=None,
outcome, feedback=None, cost=None, latency_ms=None)` writes ONE owner-scoped `run_records` row
through `_run`: identity is derived only from the AuthContext (no owner/user_id parameter),
`redact_capture` strips secrets/PII and reduces `tool_sequence` to names BEFORE the write, and
the row reuses the payload-free trace `request_id`. `run_records(context)` returns only that
owner's rows. `retrieve_memories(context, query, *, query_time, kind=None, portfolio_id=None,
limit=None)` applies ownership and status/expiry filters in SQL BEFORE ranking, then scores a
deterministic keyword overlap (`KEYWORD_WEIGHT=100`, `TYPE_WEIGHT=10`, `PORTFOLIO_WEIGHT=5`;
tie-break newer `created_at` then stable `id`) and returns rows with `score`/`matched_tokens`;
only the owner's private rows plus permitted public facts are ever scored. `delete_memory` and
`supersede_memory` authorize ownership, update status, write a `memory_deletions` lineage row
(supersede also increments `version` and sets `supersedes`), and exclude the row from
retrieval. This is SUBSTRATE ONLY and makes NO learning/adaptation claim; an un-reviewed
procedure is never retrievable as shared/public, and embeddings are deferred (future vectors
inherit the same authz + deletion semantics). See ../M5a.md.

## M3 job and outbox contract (implemented)

`jobs.JobStore` persists job ID, type, trusted `owner_ref` or `public_scope` (XOR), idempotency key, due time, lease expiry, attempt count, status (`pending|leased|done|dead`) and correlation ID. `enqueue(job_type, *, idempotency_key, due_at, correlation_id, owner_ref=None, public_scope=None)` dedups on `(type, idempotency_key)`. `claim_due(now, limit)` claims atomically over `Store.transaction()` (BEGIN IMMEDIATE), setting `status='leased'`, `lease_expires_at = now + LEASE_SECONDS`, `attempts += 1`. `recover_expired(now)` resumes expired leases after a crash; `fail(job_id, now)` re-arms a job immediately (no backoff) or routes it to `dead` once `attempts >= MAX_ATTEMPTS`; `review_queue()` lists dead jobs. Constants: `LEASE_SECONDS=300`, `MAX_ATTEMPTS=3`, `MAX_CLAIM_PER_TICK=8`.

`jobs.JobRunner(store, handlers)` is a deterministic, test-invoked poller with no background thread. `tick(now)`/`run_due(now, limit)` recover, claim and dispatch each handler inside `store.transaction()`; a normal return completes the job, an exception fails it, and one handler failure never aborts the tick. Handlers are a `{job_type: callable(job_row, store, now)}` registry discovered at construction (`build_default_handlers(store)` wires `collect` and `monitor_portfolio`). The `monitor_portfolio` handler RE-AUTHORIZES the current owner of the job's `owner_ref` via `Store._authorize` at execution time: on an ownership change it no-ops and writes nothing, and the job still completes. Public collection coalesces by `(security, source, time-bucket)` and is holder-blind; the subsequent private analysis stays owner-scoped. Crash/resume applies an idempotent effect exactly once.

`Store.enqueue_notification(user_id, event, *, dedup_key, channel, now, materiality=None)` persists a `notification_outbox` row (user, event, dedup key, channel, delivery status) BEFORE delivery (at-least-once), dedups on `UNIQUE(user_id, dedup_key)`, and sets `suppressed` with precedence quiet-hours → materiality-below-threshold → cooldown, else `pending`. `Store.deliver_pending(notifier, now)` delivers pending rows through the `ports.Notifier` seam and marks them `sent`; `suppressed` rows are never delivered. `LocalSinkNotifier` is the deterministic in-memory sink (no real channel), idempotent per `(user_id, dedup_key)` so a retry never double-sends. `PortfolioService.set_preferences`/`preferences` derive and authorize the user from the trusted context (quiet hours, materiality threshold, cooldown, channel); a forged/cross-user context is denied and a failed action records a metadata-only `invalid` trace. Unchanged conditions stay quiet: `monitor_portfolio` only enqueues on a `monitoring_state` change. Job/outbox/state rows are payload-free (type/keys/status/timestamps only).

## Execution modes

Read-only permits analysis, imports, memory and alerts but no order records. Paper permits isolated simulated activity only. M0 records buy intents against an explicit reference price and leaves actual holdings untouched. M6 introduces a separate paper account/ledger, side, cash checks, fees, fill assumptions and reproducible valuation. Mutual fund simulation must use dated NAV semantics and explicit assumptions, not stock-style intraday fills. Live mode is absent and rejected, including unknown mode values.

## Error behavior

Denied access returns a generic unavailable result. Validation errors require correction/reconciliation. Provider failures preserve last observations as stale, never fabricate replacements. Capacity failure queues a proposed enhancement in M2 while baseline holding processing continues. Model failure falls back to deterministic facts. No retry may create duplicate imports, paper fills or notifications.

## M1 application boundary

Application callers use PortfolioService with AuthContext from Authenticator.authenticate(token). No service method accepts a user-selected owner ID. OperatorService checks operator role for public ingestion and global membership. Store methods remain trusted internals. Successful service mutations and outcome traces commit atomically; failed actions roll back before a metadata-only failure trace is stored. See ../M1.md for local composition and credentials.

## M2a valuation boundary

OperatorService.ingest_observation accepts validated dated public observations. PortfolioService.value_portfolio and valuation_snapshot enforce current ownership and captured snapshot ownership. Source is explicit; stale/missing data never produces a complete total. See ../VALUATION.md for signatures, precision, freshness and snapshot contracts.

## M2 import-provenance boundary

`PortfolioService.import_holding_with_provenance(context, portfolio_id, symbol, quantity, *, source, market_as_of, idempotency_key)` authorizes ownership, resolves identity by effective-dated alias before the validator, upserts the holding, and records an import receipt plus a holding event atomically. A repeated call with the same `(portfolio_id, idempotency_key)` returns the prior security ID without re-applying. `remove_holding(context, portfolio_id, security_id, *, source, idempotency_key, market_as_of)` deletes the holding and records an audited `remove` receipt and event; removing a missing holding raises a reconciliation ValueError. `receipts(context, portfolio_id)` returns that owner's receipts. No method accepts a caller-supplied owner ID; identity comes only from the context. Trusted alias registration (`Store.register_alias`) is an internal/admin call used by fixtures, not an authenticated service method. All three service methods reuse the atomic success/failure trace path, so a failed action rolls back before a metadata-only trace is written.

## M2 completion boundary (transactions, reconciliation, admission)

`PortfolioService.record_transaction(context, portfolio_id, security_id, side, quantity, *, source, idempotency_key, occurred_at)` authorizes ownership, validates `side ∈ {buy, sell}` and a positive finite Decimal quantity, normalizes `occurred_at` to UTC, and appends one signed ledger event. It is idempotent on `(portfolio_id, idempotency_key)` and never auto-mutates holdings. `transactions(context, portfolio_id)` returns that owner's ledger rows. `reconcile(context, portfolio_id)` returns `(security_id, ledger_sum, holding_qty, reconciled)` rows comparing the signed Decimal ledger sum to the holdings snapshot; it flags divergence and never coerces the snapshot. Cross-user and forged-context access is denied, and a failed action rolls back before a metadata-only `invalid` trace that carries no side/quantity/source payload.

`OperatorService.propose_enhancement(context, security_id, *, reason)` and `OperatorService.list_monitoring(context)` are operator-gated: a non-operator context is denied by the role gate and writes nothing. `propose_enhancement` derives the operator identity from the authenticated context (never a caller-supplied ID), computes a UTC `now` at the boundary, and returns `admitted` | `queued` | `rejected` from the admit → queue → reject machine (no auto-eviction; idempotent re-proposal). Both run through the atomic trace path with fixed, payload-free operation names. The existing `enable_monitoring`/`set_research` operator methods remain for back-compat. Baseline coverage (`Store.baseline_coverage`, `Store.reconcile_baseline_coverage`) is public market-coverage status, maintained atomically with holdings and carrying no private context.
