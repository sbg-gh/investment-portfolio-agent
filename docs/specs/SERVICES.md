# Application and adapter contracts

## Internal persistence contract (M0 compatibility)

`Store.import_holding(user_id, portfolio_id, symbol, quantity, validator)` authorizes first, resolves through a validator, and atomically registers/upserts. FixtureValidator only resolves supplied synthetic records. A real validator must reject ambiguity, stale identity evidence and unsupported instrument types.

`holdings(user_id, portfolio_id)` returns scoped `(security_id, decimal-string quantity)` rows. `set_research` and `enable_monitoring` are trusted operator methods. `retrieve_memory(user_id)` returns that user's records plus public facts, rejecting unknown users. `record_paper_order(..., mode='read_only')` rejects unless mode is exactly `paper`. M0 has no brokerage port.

MarketData, LocalModel and Notifier protocols in ports.py define future integration seams, not implemented integrations. Provider adapters must enforce timeouts, retries with backoff, provenance, schema validation and data licensing constraints. Model adapters have explicit token bounds and a service-level concurrency limit. Treat retrieved documents as untrusted data, never executable instructions.

## Planned chat boundary (M1/M4)

A request contains conversation ID, message and optional authorized portfolio ID. Server-derived identity is carried in an immutable request context with trace ID. Responses include explanation, evidence references, as-of times, freshness warnings and proposed actions. Action proposals pass deterministic authorization and schema checks. Do not accept client-supplied owner IDs as authority.

## Planned job contract (M3)

Persist job ID, type, trusted owner reference or public scope, idempotency key, due time, lease expiry, attempt count, status and correlation ID. Claim atomically; resume expired leases after crashes; limit retries; route exhausted jobs to a review queue. Reauthorize private ownership on execution. Coalesce public collection by security/source/time bucket and keep subsequent private analysis separate.

Notification outbox persists user, event, deduplication key, channel and delivery status. Enforce quiet hours, materiality, per-user preferences and cooldown. Do not send periodic unchanged updates by default. Test crash between evaluation, outbox creation and delivery; use provider idempotency when available, otherwise document at-least-once delivery.

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
