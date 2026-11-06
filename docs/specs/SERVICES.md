# Application and adapter contracts

## M0 callable contract

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
