# Memory, experience learning and evaluation

## Scope and memory types

A shared memory service enforces scope before retrieval or prompt assembly. Private portfolio facts, goals, chats, execution traces, preferences and outcomes stay user-scoped. Public facts are independently sourced market knowledge. Generic procedures may be shared only after review demonstrates that no private examples, holdings, user identifiers or preferences survive.

Episodic memory records what happened in a run. Semantic memory records supported facts/preferences with provenance. Procedural memory records versioned strategies for selecting tools and handling situations. M1 stores these labels and retrieves explicitly scoped rows. M5a adds the richer record contract, owner-scoped run capture and deterministic keyword retrieval that ranks scoped rows (SUBSTRATE ONLY — no learning claim); it still does NOT extract, review, promote or automatically execute procedures (that is M5b). Public ingestion is trusted operator-only and does not sanitize arbitrary private input.

## Record contract (M5a implemented; substrate only)

Implemented in M5a as SUBSTRATE ONLY — storing these records makes NO learning/adaptation
claim. One unified `memories` table is extended in place at schema v7 with: memory ID, scope,
owner, optional portfolio, type (`episodic|semantic|procedural`), payload, source run/evidence
IDs, creation and expiry times, confidence, status (`active|superseded|deprecated|deleted`),
version, supersedes, applicable conditions and `review_status`; deletion lineage lives in
`memory_deletions`. Owner-scoped run records live in a separate `run_records` table with
request/task type, policy/model version, tool sequence, observation timestamps, outcome,
feedback and cost/latency, correlated to the payload-free execution trace by `request_id`.
Secrets are redacted and personal content minimized at capture by a deterministic fail-safe
pass (tool sequence reduced to names; feedback truncated), BEFORE any write.

Retrieval (M5a implemented) applies ownership and status/expiry filters BEFORE ranking, in
SQL, so excluded rows never reach the ranker. It uses SQLite KEYWORD retrieval and explicit
metadata only this slice (`KEYWORD_WEIGHT=100`, `TYPE_WEIGHT=10`, `PORTFOLIO_WEIGHT=5`;
tie-break newer `created_at` then stable `id`). Embeddings are added only after a benchmark
demonstrates benefit; any future vectors stay under the SAME authorization and deletion
semantics as source records. Conflicting facts retain provenance and resolution state through
source/evidence ids and the `superseded` status; model inference never silently overwrites
authoritative evidence. The extended scope CHECK keeps an un-reviewed procedure un-shareable at
the DB layer, so it is never retrievable as shared/public — automatic promotion/sharing is M5b.

## Promotion lifecycle (M5b — implemented)

Implemented in M5b (`procedures.py`, schema v8 append-only `procedure_events`; see ../M5b.md). M5a stored the procedural type and `review_status`; M5b adds deterministic extraction, the explicit transition table, versioned activation, manual and regression rollback with retained lineage, and operator-only privacy-reviewed publish. The sequence is: Observed run → candidate lesson → evidence/contradiction review →
held-out evaluation → approved version → active → deprecated/rolled back. A successful single run is insufficient for global promotion. User feedback is evidence, not a guarantee of correctness. Financial market outcomes are confounded; prioritize measurable task quality and calibrated uncertainty over short-term returns as a learning signal.

For example, a procedure may learn to request a dated NAV adapter when handling a mutual fund, replacing repeated use of an intraday quote tool. Demonstrate the changed tool sequence and lower stale-data error rate; merely retrieving a previous explanation is not behavioral adaptation.

## Evaluation plan (M5b — implemented held-out gate)

Implemented in M5b as the pre-registered three-variant harness (`m5b_harness.py`, `m5b_prereg.json`, seventh suite `m5b-learning-baseline-v1`) with exact paired Clopper-Pearson intervals, an honest `inconclusive` outcome and a numeric budget. The recorded verdict is `significant_improvement` on the synthetic scripted simulation ONLY (n=32, b=32, c=0, interval [0.8911, 1.0]); it validates the apparatus and plumbing, not real-LLM adaptation (see ../M5b.md). The M5a `m5a-memory-baseline-v1` suite
is a deterministic scope/capture/retrieval correctness gate kept DISTINCT from this held-out
plan; it carries no `learning_improvement` key and establishes no adaptation claim. Freeze
model, tools and dataset; compare no-memory, retrieval-only and approved-procedure variants on identical held-out cases. Split by time and task family, prohibit test cases from entering memory, and repeat runs with recorded seeds/settings. Include contaminated documents and adversarial cross-user requests.

Measure task success, evidence correctness, unsupported claims, stale-price misuse, tool-choice error, latency, token use, duplicate alerts and unauthorized disclosure. Include regression tests for all established scope invariants. Report per-task results and paired uncertainty intervals; do not claim improvement from aggregate anecdotes.

Initial proposed gate: zero unauthorized disclosures or live actions; no loss on deterministic accounting cases; improved primary task-success metric on held-out cases with a paired confidence interval excluding zero, within a predeclared local latency/resource budget. If the dataset is too small, report inconclusive and keep the candidate inactive. Revert on regressions and retain evaluation lineage. Set concrete latency budgets after M4 benchmarks on the target Mac.

M1 now supplies metadata-only scoped execution traces and an eight-case deterministic safety baseline. M5a adds owner-scoped run records (tool sequences, policy/model versions, redacted feedback) alongside those payload-free traces, plus the sixth `m5a-memory-baseline-v1` suite. These are all public regression fixtures, not evidence of learned improvement. See ../M5a.md and ../TESTING.md.
