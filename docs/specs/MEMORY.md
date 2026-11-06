# Memory, experience learning and evaluation

## Scope and memory types

A shared memory service enforces scope before retrieval or prompt assembly. Private portfolio facts, goals, chats, execution traces, preferences and outcomes stay user-scoped. Public facts are independently sourced market knowledge. Generic procedures may be shared only after review demonstrates that no private examples, holdings, user identifiers or preferences survive.

Episodic memory records what happened in a run. Semantic memory records supported facts/preferences with provenance. Procedural memory records versioned strategies for selecting tools and handling situations. M1 stores these labels and retrieves explicitly scoped rows; it does not extract, rank, summarize or automatically execute procedures. Public ingestion is trusted operator-only and does not sanitize arbitrary private input.

## Planned record contract

Memory ID, scope, owner, optional portfolio, type, payload, source run/evidence IDs, creation and expiry times, confidence, status, version, supersedes, applicable conditions and deletion lineage. Run records include request/task type, policy/model version, tool sequence, observation timestamps, outcome, feedback and cost/latency. Redact secrets and minimize personal content at capture.

Retrieval must apply ownership and status/expiry filters before ranking. Start with SQLite keyword retrieval and explicit metadata; add embeddings only after a benchmark demonstrates benefit. Keep vectors under the same authorization and deletion semantics as source records. Conflicting facts retain provenance and resolution state; never silently overwrite authoritative evidence with model inference.

## Promotion lifecycle

Observed run → candidate lesson → evidence/contradiction review → held-out evaluation → approved version → active → deprecated/rolled back. A successful single run is insufficient for global promotion. User feedback is evidence, not a guarantee of correctness. Financial market outcomes are confounded; prioritize measurable task quality and calibrated uncertainty over short-term returns as a learning signal.

For example, a procedure may learn to request a dated NAV adapter when handling a mutual fund, replacing repeated use of an intraday quote tool. Demonstrate the changed tool sequence and lower stale-data error rate; merely retrieving a previous explanation is not behavioral adaptation.

## Evaluation plan (M5)

Freeze model, tools and dataset; compare no-memory, retrieval-only and approved-procedure variants on identical held-out cases. Split by time and task family, prohibit test cases from entering memory, and repeat runs with recorded seeds/settings. Include contaminated documents and adversarial cross-user requests.

Measure task success, evidence correctness, unsupported claims, stale-price misuse, tool-choice error, latency, token use, duplicate alerts and unauthorized disclosure. Include regression tests for all established scope invariants. Report per-task results and paired uncertainty intervals; do not claim improvement from aggregate anecdotes.

Initial proposed gate: zero unauthorized disclosures or live actions; no loss on deterministic accounting cases; improved primary task-success metric on held-out cases with a paired confidence interval excluding zero, within a predeclared local latency/resource budget. If the dataset is too small, report inconclusive and keep the candidate inactive. Revert on regressions and retain evaluation lineage. Set concrete latency budgets after M4 benchmarks on the target Mac.

M1 now supplies metadata-only scoped execution traces and an eight-case deterministic safety baseline. These are public regression fixtures, not evidence of learned improvement. See ../TESTING.md.
