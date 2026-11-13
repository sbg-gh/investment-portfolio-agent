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
- M0 paper records are positive buy intents with reference prices, not simulated fills, balances or investment performance. M6 adds a separate paper ledger (see the M6 choices below); the M0 `paper_orders` table and `record_paper_order` are unchanged.

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
## M6 paper-simulation choices (2026-11-07)
M6 adds a separate paper ledger at schema v9. It is a simulation: nothing here claims performance, and no live mode exists.
1. No model-proposed paper orders. Paper operations are service methods that derive the owner from the AuthContext. The chat tool allow-list contains no paper tool, so a model proposal naming one is rejected and writes nothing.
2. FIFO lots. The oldest lot is consumed first. A buy fee is capitalized into the lot basis, a sell fee reduces proceeds, and a partially consumed lot's basis is rounded to 12 decimal places (half-even) with the exact residue kept on the remaining lot. Realized P&L is accounting, not performance.
3. Fees and fills. The account fee model is flat, per-share or percentage (a fraction, so 0.001 is 0.1%), applied exactly and unrounded. A stock or ETF fills at the cutoff observation (same selection rule as valuation) with no slippage and no intraday-execution claim.
4. Corporate actions deferred. The ledger has no split, dividend or merger entry types; positions do not adjust. This is a documented limitation.
5. New paper-only tables. `paper_accounts` and `paper_ledger` (v9, append-only triggers) reference only users, portfolios, securities, market observations and paper accounts. No column or key points at holdings, transactions, import receipts or holding events.
6. Rejections raise and persist nothing. Overdraft, oversell, stale and missing price/NAV raise `PaperRejected`; only the payload-free `invalid` trace is written. There is no pending state, because that would need a scheduler; retry with a new idempotency key once data arrives.
7. M0 `paper_orders` untouched. It stays a reference-price intent annotation with no cash, lots or fills, and the paper ledger neither reads nor writes it. No migration touches it.
8. Fund fill-date assumption. A mutual fund fills at the latest published NAV whose observed and fetched times are at or before the cutoff, and the NAV date is recorded. Forward pricing and the 4pm cutoff are not modeled, and a price observation is never a fund basis.
9. Authorizer proof. Isolation is shown by byte-identical dumps of the real-state tables after broad paper activity, and by running the whole paper workload on a connection whose SQLite authorizer denies every read and write of those tables.
10. Mode gate. Mutators accept exactly the string `paper`; the default is `read_only`, and unknown or live values are rejected.
## M7 local web UI choices, slice 7a (2026-11-12)
1. Backend transport. uvicorn 0.54.0 with h11 0.16.0 and click 8.5.1 (exact pins, hashed `requirements/ui.lock`) as transport only, behind an optional `ui` extra. Routing, body/JSON, Host/Origin/CSRF, sessions, rate limits, static allow-list and SSE are our own framework-neutral code, so the HTTP-layer tests and the ninth eval run with no third-party package. Starlette, FastAPI/pydantic (number coercion fights Decimal strings) and bare `http.server` (no limits) were rejected.
2. Frontend stack. React 19.3.1, TypeScript 5.9.3, Vite 6.4.6, Vitest 3.2.7, jsdom 26.1.0, Testing Library, jest-axe, ESLint 9 with jsx-a11y; hash router; no UI kit and no charting library (SVG bars from BigInt basis points with a table twin, because chart libraries scale with floats).
3. Node 18 dev-advisory acceptance. The toolchain stays on Node 18.20.2. Every Node-18-compatible vitest is inside published advisories (tinypool, `@vitest/mocker`), and the eslint chain carries minimatch/brace-expansion advisories. `npm audit --omit=dev` reported 0 vulnerabilities; the full audit reported 14 (all build-time tooling). Accepted as dev-only risk; the fix is moving to Node 22 LTS.
4. Sessions. In memory, process-local, no schema change: 256-bit id stored as a SHA-256 digest, credential id plus CSRF token, 30 min idle, 8 h absolute, capped by credential expiry, re-authenticated per request so revocation applies at once. A sessions table was rejected (migration, persisted secrets, no local benefit).
5. Packaging. The frontend is built on demand into the gitignored `src/portfolio_agent/web/static/`; the server serves a "UI not built" page without it. A committed bundle was rejected (unreviewable diffs, drift).
6. Lockfiles. `ui/package-lock.json` and `requirements/ui.lock` are committed; `node_modules` never is.
7. Operator-attested registry. No authoritative instrument provider exists. Operators register instruments (`source='operator-attested'`); holdings import resolves symbols only through that registry and unknown symbols need registration or reconciliation. Prices and NAVs are operator-entered; there is no live market data.
8. Chat transport. Staged SSE (`status`, keep-alives, `evidence`, `explanation`, `proposals`, `done`), not token streaming, because the M4 gate parses the complete reply first. Model failure degrades to deterministic facts (`on_model_error='facts'`, `model_status: unavailable`). When a real model is configured, the server adds an opt-in `prompt_preamble` asking for the JSON shape the gate parses; the M4 prompt and gate are otherwise unchanged and the offline/default prompt has no preamble.
9. Loopback only by default; any other bind needs three explicit overrides and still has no TLS. Secure cookie flag only over HTTPS.
## Starter data seed choices (2026-11-13)
No schema change (v9 stays). Open decisions (a)-(d) were settled by the proposals below; the user's decisions were real symbols, a synthetic sample portfolio, and both a command and buttons.
1. Command: `python -m portfolio_agent.web seed-starter-data --db PATH [--user USER_ID]`, trusted local administration beside `create-user`. It always registers instruments (idempotent); `--user` adds that user's sample. It prints counts only and needs no ui extra.
2. Two routes, not one. `POST /api/operator/starter/instruments` is operator-only (session, CSRF, route flag and a second role check in the service). `POST /api/starter/sample-portfolio` is for any signed-in user, creates for the session user only, never registers instruments, and returns 409 `starter_instruments_missing` with nothing created. A combined route would let a non-operator trigger shared registration or leave other users without a sample.
3. No prices, synthetic or otherwise, and no fake tickers. A made-up price for a real ticker is invented market data (AGENTS.md); the sample valuation honestly shows missing lines and no total until an operator enters prices.
4. Naming. Portfolio `Sample portfolio (synthetic)`, 20 symbols at quantity `100`; receipts source `starter-sample-synthetic`, keys `starter-sample-v1:<SYMBOL>`; instrument ids `op:<lowercase symbol>` (the existing registry scheme); provenance in the existing `securities.source` column as `operator-attested:ai-conviction`, with `:user-attested` appended for SKHY. A symbol already registered by hand is counted as present and left unchanged; an id collision with a different symbol fails the whole seed and rolls back.
5. The sample is found by owner, exact name and starter receipts, so a user's own same-named portfolio is never adopted. The seed data is package data (`starter_data.json`), loaded with stdlib only, so the zero-dependency core is unchanged.

## CSV price import instead of a provider adapter (2026-11-13)
No schema change (v9 stays), no new dependency, no network call. This replaces an aborted Tiingo adapter slice. The user's decisions: pause provider integration, build CSV import, and add an explicit freshness window.
1. Providers evaluated and not adopted. Research was done on 2026-11-13 (keyless sources about 15:50-15:55 UTC; Tiingo documentation read the same day, no token used).
   - Tiingo (https://www.tiingo.com/tos, section 1.6(a)): Starter/free and trial plans may not save, archive, back up or otherwise retain Tiingo Data or derived products in persistent storage. Paid plans may persist data only while subscribed and must delete it on cancellation or downgrade. This application stores observations, snapshots and backups, so a free plan cannot be used.
   - Yahoo Finance chart (unofficial): its terms bar automated collection without permission and `robots.txt` is `Disallow: /`.
   - Nasdaq historical (unofficial, undocumented): it worked technically, but `robots.txt` is `Disallow: /` and the terms page could not be fetched, so permission is unclear.
   - Stooq: plain HTTP got an HTML JavaScript proof-of-work bot challenge (0 of 5 symbols); its terms were not verified.
   - Cboe delayed quotes: a Cloudflare 307 redirect that was not followed; terms not checked; delayed quotes rather than end-of-day history.
   What remains unverified: the full terms pages of Nasdaq and Stooq, Cboe terms, whether a paid Tiingo plan fits this application's storage and backup model, Tiingo's behavior for unknown tickers and for SKHY (no token was used), and any other provider.
2. Conditions for a future adapter: the provider's terms permit the storage this application does; the token comes from an environment variable; only ticker symbols leave the machine; a fixed host; TLS verification on; timeouts; backoff on transient errors only; response schema validation. Provider adapter and scheduling stay deferred (MILESTONES.md).
3. CSV format. Required columns `symbol`, `date`, `close` (exact names, case-insensitive, any order, no aliases). All other columns are ignored: only their sanitized names are reported and their values are never read or stored. `date` is `YYYY-MM-DD` and means 16:00 America/New_York that day, converted to UTC with stdlib `zoneinfo` (EST 21:00Z, EDT 20:00Z). There is no holiday calendar, so weekend and holiday dates are accepted as given and early-close days are still treated as 16:00. `close` is the raw close as a plain decimal (no thousands separators, `$`, exponent, sign, NaN or infinity); zero and values outside the valuation range rules are `invalid_value`. Stocks and ETFs only; mutual-fund NAV import is out of scope. Currency USD, basis `price`.
4. Import semantics. The operator names one source for the whole file. Valid rows are imported atomically in one transaction through `valuation.ingest_observation`; every rejected row is reported and never blocks valid rows (all-or-nothing was rejected because one bad symbol in a brokerage export would block the file). Unknown symbols are never auto-registered. Every occurrence of a duplicate (symbol, date) pair is rejected. Against the latest existing observation for the same security, source and observed time: equal value is `unchanged` (a re-run is idempotent), a different value is `corrected` and is written as a NEW observation with the import's `fetched_at`; the old row is kept, and valuation at an earlier cutoff still sees it. This fits the existing rule (identity includes `fetched_at`; selection takes the latest `fetched_at` at or before the cutoff). The only existing conflict (same `fetched_at`, different value) can occur only with an identical injected clock and is reported as `conflict`, not written.
5. Fixed result vocabulary: `imported`, `unchanged`, `corrected`, `conflict`, `unknown_symbol`, `invalid_value`, `invalid_date`, `future_date`, `duplicate_row`, `unsupported_kind`, `invalid_format`; file-level `invalid_source`, `invalid_encoding`, `file_too_large`, `too_many_rows`, `empty_file`, `invalid_header`, `invalid_csv`, `timezone_unavailable`. A row reports the first failing check in this order: `invalid_format`, `duplicate_row`, `invalid_date`, `future_date`, `invalid_value`, `unknown_symbol`, `unsupported_kind`. Results carry only line number, symbol (when it has the symbol shape) and code: never file content, values or exception text.
6. Surfaces. Operator-only route `POST /api/operator/prices/import` with `{source, csv_text, dry_run}` (JSON, no multipart parser; the 65536-byte body cap is unchanged, about 2,000 rows) and CLI `import-prices` for larger files (1 MiB, 10,000 rows). The CLI is trusted local administration like `seed-starter-data`; exit 0, 3 (some rows rejected) or 1 (file-level error). The UI allows Import only after a Validate of the identical text; the server always re-validates.
7. Freshness window. Valuation accepts `max_age_days` (digits only, 1 to 30). It sets the price window to N days and the NAV window to `max(N, 3)` days, so NAV is never tightened below its 3-day default. Absent means the unchanged default (24 h price, 72 h NAV). The saved snapshot records the policy used and the total is still withheld whenever any line is stale or missing. UI choices are 1, 3, 5, 7 and 14 days.
8. Operator license responsibility. The app does not verify the data or its license. The operator is responsible for having the right to use and store what they import; the UI and docs say so. No file with real tickers and made-up prices ships: only a header-only template.
## Yahoo Finance end-of-day fetch: informed acceptance (2026-11-13, 18:56 UTC)
No schema change (v9 stays), no new dependency (stdlib `urllib`/`http.client`). The CSV import stays the path for licensed data; this adds a manual, operator-only fetch that feeds the same import pipeline. Research is in `.agents/tasks/eod-price-source-report.md` (recorded 2026-11-13).
1. What the terms say. Yahoo's Terms of Service (https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html, last updated 1 November 2026) bar accessing or collecting data from the services using automated means such as robots, spiders and scrapers for any purpose without express prior permission. `robots.txt` on the endpoint is `Disallow: /`. The chart endpoint is unofficial and undocumented, has no SLA, and the Yahoo API terms for registered APIs do not cover it. Permission was not requested or granted.
2. The user's decision. After this was explained, including that the source is unofficial, may break or block at any time, and that automated retrieval is a knowing breach of those terms, the user chose to proceed with a manual Yahoo fetch. The accepted risks are blocking, breakage and the terms breach, for personal, manual, on-demand, non-redistributed use. The data is representative and non-authoritative. Nothing is scheduled, and the data is never shared with other parties.
3. What leaves the machine: the ticker symbol in the URL path and the fixed query `range=5d&interval=1d`, plus an honest `User-Agent` (`portfolio-agent/<version> personal local-first app`). No cookies, tokens, crumb, credentials or portfolio data. No attempt is made to defeat bot protection: on 401, 403, 429 or an HTML challenge the whole press stops with a fixed `blocked` or `rate_limited` code and is never retried.
4. Network rules. Fixed host `query1.finance.yahoo.com` over HTTPS only; TLS certificate verification on; no redirect of any kind is followed (a redirect is `provider_error`); connect and read timeouts; a response size cap and a JSON content-type check; the symbol must be a registered stock or ETF and is URL-encoded; at least 1.5 s between requests; at most 40 requests per press with retries counted (the rest are `skipped_budget`); up to 2 retries with exponential backoff and jitter on timeouts, connection errors and 5xx only, never on 4xx or schema failures.
5. Data choices. `range=5d`, `interval=1d`, every completed bar kept (5 days spans a long weekend and gives the freshness window room; repeat presses are idempotent). `quote.close` (split-adjusted, not dividend-adjusted) is used, not `adjclose`. The endpoint returns binary floats (for example `508.5799865722656`); each is converted with `Decimal(repr(x))` and quantized to 4 decimal places half-even, so stored prices carry at most 4 decimals. Each bar maps to its session date in `meta.exchangeTimezoneName`, then to 16:00 America/New_York of that date in UTC through the CSV importer's function (one convention). A bar whose session is unfinished (session date equals the current exchange-local date and now is before `currentTradingPeriod.regular.end`, or 16:00 New York if absent) is dropped, because Yahoo returns a partial close for the open session. A fetched price is therefore always a completed close. Currency must be USD and `instrumentType` EQUITY or ETF; anything else is not fetched as a price. Fetched rows pass through the CSV import core with source `yahoo-chart`, so unchanged, corrected, duplicate and atomic semantics are identical. Mutual funds (NAV) are out of scope.
6. Execution model. A press takes 30 to 60 seconds and the web layer serves every request through one worker thread that owns the SQLite store. The network phase therefore runs on a separate background thread with no store and no transaction; only one short ingest transaction runs on the worker, where identity and operator role are re-checked. One fetch at a time (409 `busy`), POST returns 202 with a fetch id, and the UI polls an in-memory, operator-scoped status. Only the latest record is kept, and a server restart loses it by design (status then reports `unknown`, the UI shows "interrupted"). Dry-run makes real requests, counts as a press for the busy rule and the request cap, and writes nothing.
7. Outcome codes (fixed, never provider or exception text): `imported`, `unchanged`, `corrected`, `not_found`, `unsupported_kind`, `rate_limited`, `blocked`, `provider_error`, `invalid_response`, `skipped_budget`, `skipped_stopped`. A failed symbol keeps any earlier observation (which ages) and is never filled with a guess. Logs carry codes and counts only.
8. The port. `MarketData.latest()` is single-valued; this adapter returns bars, so it follows the port's rules (timeouts, backoff, provenance, validation) without implementing the interface. `ports.py` is unchanged.
