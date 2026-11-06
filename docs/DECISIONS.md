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
