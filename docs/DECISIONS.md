# Decision register and provenance

Source: user requirements and visible user turns in “Propose Agent Architecture”, conversation `6aedf1bb-aecc-83e9-9178-d5f36d5bff95`, retrieved 2026-11-06. Assistant messages were returned as content-reference placeholders, with no accessible bodies or attachments. This package does not claim to reproduce unseen details. The explicit requirements below are preserved; implementation choices are proposed defaults.

## Confirmed requirements

| ID | Decision | Consequence |
|---|---|---|
| D01 | Multiple users, private portfolios | Tenant scoping is mandatory throughout the data path |
| D02 | Personal US stocks, ETFs, mutual funds | No options, crypto, derivatives or non-US expansion initially |
| D03 | Research universe target 400 | Planning target, separate from security master |
| D04 | Enhanced monitoring capacity 75 | Bounded high-cost monitoring tier |
| D05 | Auto-register validated holdings | Valid holdings outside research universe remain supported |
| D06 | Hybrid chat and proactive background work | Both call common application services |
| D07 | Lightweight local models on Mac mini | Local inference with bounded resource budgets |
| D08 | Shared memory and experience learning | Common scoped memory services and measured adaptation |
| D09 | Read-only/paper initially | No live order execution |
| D10 | Modular, vendor-neutral, local-first | Swappable adapters, portable storage and protocols |

## Proposed defaults requiring review during their milestone

- 75 slots count distinct securities across the deployment, not per user. Shared data collection is deduplicated; ownership, reasons for prioritization and notifications remain private. A changed interpretation needs a decision update and capacity migration.
- 400 is a soft global research target, not a hard cap. Holding registration does not imply research inclusion or enhanced monitoring admission.
- Baseline coverage includes every held security even above both thresholds. Providers may limit cadence; degraded coverage must be visible, never silently omitted.
- “US” means an authoritative provider classifies the instrument as US within supported types. Resolve ADR, cross-listing, domicile and venue eligibility explicitly before real import; fixtures use country=US only.
- SQLite and Python standard library form M0. No vector database, orchestration framework, message broker or model vendor is mandatory.
- Start with one local service process, one inference request at a time, and bounded worker queues. A loopback model adapter is planned; benchmark models against actual Mac memory before choosing one.
- No private-to-public automatic memory promotion. Public facts enter through trusted public-data ingestion; generic learned procedures require review and privacy checks.
- M0 paper records are positive buy intents with reference prices, not simulated fills, balances or investment performance.

Unresolved: data-provider licensing and coverage, model/quantization choice, retention duration, alert thresholds, portfolio accounting method, authentication mechanism and notification channel. Resolve each before its owning milestone is accepted.

## M1 implementation decisions (2026-11-06)

Local bearer credentials are random, hashed in storage, revocable and expiring. Signed short-lived contexts carry authenticated identity into application services. HTTP transport is deferred. Public/private memory scope is explicit; all ambiguous legacy NULL-owner records are quarantined. Schema upgrades are atomic and versioned. Restore revokes credentials. Metadata-only execution traces and deterministic safety evals start in M1; learned-procedure evaluation remains M5. New behavior follows red/green/refactor test-driven development. These are implementation choices, not additional claims about the unavailable prior conversation.

## M2a valuation choices (2026-11-06)

Value current holdings against an explicit source and market-data cutoff; do not imply historical portfolio reconstruction. Observed and fetched timestamps both gate eligibility. Stock/ETF prices and dated fund NAV use separate bases. Default elapsed-age windows are 24 and 72 hours, explicitly stored and configurable; market-calendar semantics are deferred. Missing/stale data prevents a complete total; an indicative priced subtotal remains labelled. Store private snapshots with quantities, observation evidence IDs and holdings hashes. All calculations remain deterministic Decimal operations; no financial performance or learning claim follows from fixture success.
