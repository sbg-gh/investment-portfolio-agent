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

## M2 import provenance choices (2026-11-06)

Two decisions were approved before implementation:

1. Import-receipt storage uses one atomic schema v4 migration adding three dedicated tables (`import_receipts`, `holding_events`, `security_aliases`). The `holdings` table stays the current-state snapshot. Extending `holdings` or packing JSON payloads was rejected because it mixes current state with audit history.
2. Effective-dated alias resolution runs before the authoritative validator and only maps a symbol to an already-registered `security_id` when a non-expired window `[effective_from, effective_to)` (effective_to NULL = open; newest effective_from wins, tie-break by rowid) covers the as-of date. On a miss, import falls through to the validator's existing auto-registration path. Aliases never override or invent authoritative identity. An authoritative-override short-circuit was rejected because it would violate the "validate through the authoritative adapter before auto-registration" invariant.

Reimport no longer silently overwrites a holding's quantity: imports carry source and market as-of provenance, record an import receipt and a holding event, and are idempotent on a caller-supplied `(portfolio_id, idempotency_key)`. Removal is explicit and audited; removing a holding that does not exist raises a reconciliation error rather than a silent no-op. Identity still comes only from the authenticated context, and every operation authorizes ownership first. These are deterministic regression fixtures, not held-out learning evidence; passing establishes no adaptation claim.

## M2 completion choices (2026-11-06)

The remaining M2 work closes the milestone. Schema bumps v4 → v5 under the same atomic upgrade, FK-check-before-commit, future-version-reject and backup/restore-plus-rollback behavior as v4, adding three tables: `transactions`, `monitoring_admissions` and `baseline_coverage`. Three decisions were approved before implementation:

1. Transaction ledger reconciles to holdings, it does not drive them. `transactions` records signed buy/sell Decimal quantity events keyed `UNIQUE(portfolio_id, idempotency_key)`. "Financial fixtures reconcile" means that per `(portfolio_id, security_id)` the signed transaction-quantity sum equals the holdings snapshot quantity; `reconcile_portfolio` flags divergence and never silently coerces the snapshot. Tax lots, cost basis, cash and fees are deferred to M6. Overloading `holdings` or auto-mutating it from the ledger was rejected because it conflates current state with the event history.

2. Admission is an admit → queue → reject machine with no auto-eviction. The first `MONITORING_CAPACITY` (75) distinct securities are admitted into the active `monitoring` set (still the active-tier source of truth, backed by the `monitoring_cap` trigger); the next distinct securities are queued FIFO up to `ADMISSION_QUEUE_CAPACITY` (default 25, tie-break on stable `security_id`); beyond that a proposal is rejected and nothing is written. Re-proposing an already admitted/queued security is idempotent. Automatic eviction/replacement was rejected for M2: reassessment cadence and hysteresis remain future work. A full tier never blocks a valid holding.

3. Baseline coverage is deterministic and maintained atomically with holdings. A security held by ≥1 portfolio has `baseline_coverage.covered=1` independent of the monitoring tier; `reconcile_baseline_coverage` asserts the covered-set equals the distinct-held-securities-set (coverage rows are dropped when the last holder is removed). Durable collection scheduling (leases/outbox/retry) is explicitly M3, not M2: M2 answers "is every held security covered?"; M3 owns acting on that coverage durably.

These are deterministic regression fixtures (`m2-completion-baseline-v1`), kept distinct from held-out learning evaluation; passing establishes no adaptation claim.

## M3 proactive-service choices (2026-11-06)

M3 adds the durable machinery that acts on M2 coverage. Schema bumps v5 → v6 under the same atomic upgrade, FK-check-before-commit, future-version-reject and backup/restore-plus-rollback behavior as v4/v5, adding four tables: `jobs`, `notification_outbox`, `notification_preferences` and `monitoring_state`. Four decisions were settled before implementation, plus reauthorize-at-execution and public coalescing:

1. Scheduler. A deterministic, test-invokable `JobRunner.tick(now)`/`run_due(now, limit)` poller over the existing `Store.transaction()` (BEGIN IMMEDIATE), with no background thread; the demo and tests drive the tick explicitly, bounded by `MAX_CLAIM_PER_TICK`. A threaded or wall-clock scheduler was rejected for M3 because it would make the acceptance gates nondeterministic.

2. Leases, retry and dead-letter. Named module constants in `jobs.py`: `LEASE_SECONDS=300` (mirrors the five-minute auth-context ceiling), `MAX_ATTEMPTS=3`, `MAX_CLAIM_PER_TICK=8`, with no backoff — an expired lease makes a job immediately due again (deterministic due-time only). The dead-letter/review queue is a `status='dead'` VALUE on the jobs table (`status IN ('pending','leased','done','dead')`), not a separate table; `review_queue()` lists dead jobs. A separate dead-letter table and exponential backoff were rejected as unnecessary for a deterministic local poller.

3. Outbox seam. Reuse the existing `ports.Notifier` protocol; `LocalSinkNotifier` records deliveries to an in-memory list (the deterministic sink, not a real channel). The outbox row is PERSISTED before delivery (at-least-once), and `UNIQUE(user_id, dedup_key)` prevents a double-send so a retry never creates a duplicate notification. A real channel or network adapter was deliberately excluded.

4. Monitoring rule set. A minimal deterministic set over EXISTING data only: stale-coverage (a held + baseline-covered security whose latest observation is stale/missing per `FreshnessPolicy`, surfaced exactly as `valuation.py` does, never fabricated) and materiality-threshold crossing (Decimal change ratio between the two most recent observations vs the per-user `materiality_threshold`). Unchanged conditions stay quiet because rule evaluation compares against a recorded `monitoring_state` keyed deterministically. Concentration, allocation drift and corporate-action rules were deferred.

Reauthorize-at-execution: a per-user monitoring/notification job re-derives the CURRENT owner of its `owner_ref`/portfolio via `Store._authorize` at run time; if ownership changed after enqueue, the job does not read or notify on the old owner's private data. The job still completes cleanly (no retry storm). Coalescing: public collection coalesces by `(security, source, time-bucket)` so it runs once regardless of how many users hold the security; the subsequent private per-user analysis and outbox stay separate and owner-scoped, and collection results never reveal which users hold the instrument.

These are implementation choices, not additional claims about the unavailable prior conversation. The M3 fixtures (`m3-proactive-baseline-v1`) are deterministic regression gates kept distinct from held-out learning evaluation; passing establishes no adaptation claim (no `learning_improvement` key).

## M4 hybrid-chat and local-model choices (2026-11-06)

M4 adds an authenticated, READ-ONLY chat boundary over the common application services. Three decisions were settled before implementation. M4 adds NO schema (`SCHEMA_VERSION` stays 6).

A. No agent framework in the authority path (Option A). The model only PROPOSES; deterministic code authorizes and executes. Control flow is model proposal (untrusted text) → deterministic schema validation (`tools.validate_proposal`) → a fresh M1 re-authorization (`Store._authorize` via the AuthContext) → execution of a READ-ONLY tool. THE CENTRAL SECURITY PROPERTY: a malicious instruction smuggled via the user message, portfolio/instrument/memory data pulled into context, or a tool result may change what the model SAYS but never what the system DOES. Security never depends on the model honoring a format — an unparseable, aliased, malformed or cross-user proposal is dropped and nothing executes. An agent framework holding authority or performing one-way handoffs was rejected because it would move the authority decision out of deterministic code.

B. Vendor-neutral OpenAI-compatible adapter selected by env var, stdlib only. `OpenAICompatibleModel` POSTs `/v1/chat/completions` through `urllib` (never `openai`/`requests`); runtime `dependencies` stays `[]`. `BoundedModel` enforces a `MODEL_MAX_CONCURRENCY` semaphore and a `MODEL_MAX_OUTPUT_TOKENS` cap; conservative Mac-mini defaults are `MODEL_MAX_CONCURRENCY=1`, `MODEL_CONTEXT_BUDGET=4096`, `MODEL_MAX_OUTPUT_TOKENS=512`, and the token estimate is `chars/4` (no tokenizer dependency). Ollama / `llama3.2:3b` on `http://localhost:11434/v1` is ONE swappable local instance, used only by the guarded benchmark; an injectable `ScriptedModel` keeps the demo, eval suite and tests fully offline. Binding to a single model vendor or a required network client was rejected as contrary to the vendor-neutral, local-first invariant.

C. No v7 schema and no conversation persistence this slice. `conversation_id` is an opaque client-supplied correlation token echoed back unchanged; the response `request_id` reuses the existing payload-free `execution_traces.request_id`, and the `chat` turn records only a metadata-only trace (operation/outcome/policy version/request id/timing). Persisting conversation messages was rejected because it would store private bodies, holdings, symbols or sources, which AGENTS.md forbids by default and the contract does not require.

These are implementation choices, not additional claims about the unavailable prior conversation. The M4 fixtures (`m4-chat-baseline-v1`) are deterministic regression gates kept distinct from held-out learning evaluation; passing establishes no adaptation claim (no `learning_improvement` key).

## M5a memory-substrate choices (2026-11-06)

M5 is SPLIT. M5a delivers the durable, privacy-preserving memory SUBSTRATE and scoped
retrieval only; it makes NO learning/adaptation claim. Extraction, the
candidate→review→held-out-evaluation→approved→active→deprecated promotion lifecycle,
versioned-procedure activation/rollback and the ablation harness are M5b. Four decisions were
settled before implementation:

1. M5-split. M5a is substrate-only (record contract, capture, scoped retrieval, deletion
   lineage). M5b owns extraction, promotion/rollback and the held-out improvement gate. The
   MEMORY.md held-out gate is explicitly NOT met by M5a; passing the deterministic
   `m5a-memory-baseline-v1` fixtures establishes no adaptation claim (no `learning_improvement`
   key). Retrieving a prior note is not behavioral learning.

2. Unified vs separate memory table. One unified `memories` table is EXTENDED in place at
   schema v7 (not a parallel table), reconciling with the M1 scope/quarantine model. New
   nullable/defaulted columns carry the richer contract; the scope CHECK is EXTENDED so a
   `public` row requires `semantic` OR (`procedural AND review_status='approved'`), making an
   un-reviewed procedure un-shareable at the DB layer. `execution_traces` gains ZERO columns
   (M1 payload-free guarantee literally unchanged); run records go to a separate owner-scoped
   `run_records` table correlated by `request_id`, and deletion lineage to `memory_deletions`.
   A parallel memory table was rejected because it would duplicate the M1 scope/quarantine
   constraints and split authorization across two code paths.

3. Keyword-only retrieval. Scoped retrieval filters ownership and status/expiry in SQL BEFORE
   ranking, then scores a deterministic keyword overlap (`KEYWORD_WEIGHT=100`,
   `TYPE_WEIGHT=10`, `PORTFOLIO_WEIGHT=5`; tie-break newer `created_at` then stable `id`).
   Embeddings/vectors are deferred until a benchmark demonstrates benefit; the forward
   constraint is that any future vectors inherit the SAME authorization and deletion semantics
   as source rows and are never a bypass. A vector store this slice was rejected as premature
   and contrary to the local-first, add-embeddings-only-after-benefit guidance.

4. Capture redaction/minimization. A single deterministic, fail-safe `redact_capture` runs at
   capture BEFORE any `run_records` write; it strips credential/token-like strings,
   `password=`/`Authorization` fragments, emails, phones and SSN-shaped numbers, truncates
   free-text feedback to `MAX_FEEDBACK_CHARS`, and reduces `tool_sequence` to tool NAMES only.
   It operates on captured fields only, not as a classifier for arbitrary private input. A
   model-based or best-effort redactor was rejected because capture-time privacy must be
   deterministic and offline.

Identity is derived only from the AuthContext for capture, retrieval, deletion and supersede;
no caller-supplied owner id is trusted. These are implementation choices, not additional claims
about the unavailable prior conversation.
## M5b experience-learning choices (2026-11-07)
These were fixed in the plan and the pre-registration BEFORE any result existed.
1. Deterministic, rule-based extraction. Candidates come from the owner's own run records only (stale-data runs of tool A vs successful runs of tool B within `valuation:<kind>`); feedback text is never read; the output is an unreviewed procedural memory. Model-assisted extraction is deferred and its output would be untrusted. A model extractor was rejected for this slice because it cannot be reproduced offline.
2. Pre-registered analysis. `m5b_prereg.json` (seed 20260601, 3 repeats, alpha 0.05, n_min 20, n_target 32, family counts, lag rule `i % 4 days`, primary metric, decision rule, budget) was committed alone and first; its sha256 is frozen by a test and recorded in the baseline. Changing any value to move a verdict is forbidden.
3. Exact paired interval. Exact two-sided Clopper-Pearson on b/(b+c) (exact McNemar), stdlib, no resampling, no seed. Chosen for tiny n over a normal or bootstrap interval. Bootstrap is used only for DESCRIPTIVE continuous metrics.
4. Honest inconclusive is valid. `inconclusive` (n < n_min, no discordant pairs, or an interval containing 0.5) keeps the candidate inactive and is an acceptable passing outcome. The harness is shown to return significant_improvement, inconclusive and significant_regression on controls, so it is not hard-wired.
5. Latency/resource budget anchored on the M4 benchmark with 1.25 headroom: p50 <= 791 ms, p95 <= 5970 ms, RSS growth <= 34.5 MB, prompt <= 4096 tokens. Reports record only `budget_ok`.
6. One append-only v8 table. `procedure_events` (BEFORE UPDATE/DELETE abort) holds lifecycle state, actor, trigger and a numeric evaluation digest. Reusing M5a `memories.status`/`memory_deletions` could not express the states or lineage.
7. Simulation fairness. `PolicyModel` switches to `dated_nav` only via the system-assembled `[APPROVED_PROCEDURES]` channel; `[MEMORY]` is untrusted data and never changes tool choice. This is an assumption of the simulation, stated in docs: the significant verdict validates the apparatus and plumbing, not real-LLM adaptation.
8. The `held_out_evaluation` block exists only in the M5b report; m1..m5a stay regression gates. The M5 gate is recorded as met on the synthetic simulation only.
