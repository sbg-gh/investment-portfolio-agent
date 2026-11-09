# M5a memory substrate implementation

## Substrate only — no learning claim

M5a is the durable, privacy-preserving memory SUBSTRATE and scoped retrieval only. It stores
a richer memory + run-record contract, captures runs with redaction/minimization, and
retrieves scoped rows by deterministic keyword match. It makes **NO learning/adaptation
claim**. Retrieving a prior note or explanation is not behavioral learning. Extraction of
candidate lessons from runs, the candidate → review → held-out evaluation → approved → active
→ deprecated promotion lifecycle, versioned-procedure activation/rollback, the ablation
harness and any held-out improvement measurement are **M5b** and are out of scope here. The
M5 held-out improvement gate in MILESTONES.md is NOT met by this slice.

Forward constraint: embeddings/vector search are deferred. If vectors are ever added they
inherit the SAME authorization and deletion semantics as the source rows — private stays
private, deleted/expired/superseded stays excluded — and are never an authorization bypass.

## Record contract (unified table) and v7 migration

The richer record contract reconciles with the existing M1 `memories` table rather than adding
a parallel table: one unified `memories` table is extended in place at schema **v7**. New
columns are `portfolio_id` (owner-scoped, FK to `portfolios`), `payload`, `source_run_id`,
`evidence_ids`, `expires_at`, `confidence`, `status` (`active|superseded|deprecated|deleted`,
default `active`), `version` (default 1), `supersedes`, `applicable_conditions`, and
`review_status` (`unreviewed|approved`, default `unreviewed`). The scope CHECK is EXTENDED so a
`public` row requires `kind='semantic'` OR (`kind='procedural' AND review_status='approved'`):
an un-reviewed procedure is therefore un-shareable at the DB layer. The M1 private/public
constraints, the `memory_owner` index, `memory_quarantine` and null-owner rejection are
unchanged.

Run records live in a separate owner-scoped `run_records` table (id, `user_id` NOT NULL,
`request_id`, `task_type`, `policy_version`, `model_version`, `tool_sequence`,
`observation_timestamps`, `outcome`, `feedback`, `cost`, `latency_ms`, `captured_at`),
correlated to the payload-free execution trace by `request_id`. Deletion lineage lives in
`memory_deletions` (id, `memory_id`, `previous_status`, `reason`, `scope`, `deleted_at`).
`execution_traces` gains ZERO columns, so the M1 payload-free guarantee is literally unchanged.

The v7 migration runs on the existing atomic path: `BEGIN IMMEDIATE`, per-version apply,
`PRAGMA foreign_key_check` before commit, and future-version rejection. The scope CHECK cannot
be added by `ALTER`, so `memories` is rebuilt by the same table-rebuild idiom used for the v2
scope schema (new table with the extended CHECK, copy rows, drop, rename, recreate the index).
A broken v7 step rolls back to v6 with v6 data intact; a future `user_version` is rejected; and
backup/restore carries the new tables forward, passes integrity and `foreign_key_check`, and
still revokes every credential on restore.

## Owner-scoped run capture and the privacy split

`PortfolioService.capture_run(context, *, task_type, policy_version, tool_sequence, ...)`
derives identity ONLY from the AuthContext — there is no owner/user_id parameter and no
caller-supplied owner id is ever trusted. It runs through `PortfolioService._run`, so the
metadata-only `capture_run` execution trace is written exactly as before (operation, outcome,
policy version, request id, timing — no bodies, symbols, holdings or payloads). The run record
is an ADDITIONAL owner-scoped row that reuses the trace `request_id` as its correlation token.
Private run payloads stay owner-scoped and are not logged by default. `run_records(context)`
returns only that owner's rows; a forged or cross-user context is denied.

A single deterministic `redaction.redact_capture` runs at capture BEFORE the `run_records`
write. It is conservative/fail-safe — when a value matches any pattern it is stripped — and it
operates only on the captured fields, not as a classifier for arbitrary input:

- STRIPPED to `[REDACTED:<kind>]`: credential/token-like strings (long base64url/hex runs,
  `secrets.token_urlsafe`-shaped values), `password=`/`Authorization: Bearer` fragments,
  emails, phone numbers and SSN-shaped numbers.
- MINIMIZED: free-text `feedback` is truncated to `MAX_FEEDBACK_CHARS`; `tool_sequence` keeps
  only tool NAMES — a `{name, args, result}` entry collapses to its name string, so tool
  arguments and results never persist.

The M1 trace guarantee is NOT regressed: `execution_traces` keeps exactly its M1 column set
and still records the metadata-only outcome for the captured operation.

## Scoped keyword retrieval (filter before rank)

`PortfolioService.retrieve_memories(context, query, *, query_time, kind=None,
portfolio_id=None, limit=None)` applies OWNERSHIP and STATUS/EXPIRY filters in SQL BEFORE any
scoring, so an excluded row never reaches the ranker. The WHERE clause admits only
`(scope='private' AND user_id=?) OR scope='public'`, with `status='active'` and
`(expires_at IS NULL OR expires_at > query_time)`; a supplied `portfolio_id` is re-authorized
and adds an owner-scoped filter. Only then is a deterministic keyword score computed in Python
over the filtered rows: normalized query tokens (lowercase, punctuation stripped, split on
whitespace) are matched against each row's token set (`body` + `applicable_conditions`), scored
`KEYWORD_WEIGHT=100` per distinct matched token, plus `TYPE_WEIGHT=10` on a `kind` match,
`PORTFOLIO_WEIGHT=5` on a `portfolio_id` match, and a small bounded confidence term (0 when
NULL). Ordering is score desc, then newer `created_at`, then the stable `id`. Each returned row
carries its `score` and `matched_tokens`. Identical query + `query_time` yields identical
ordering (no wall-clock or randomness). Keyword-only this slice; embeddings are deferred.

Conflicting facts retain provenance and resolution state through `source`, `evidence_ids`,
`supersedes` and the `superseded` status; model inference never silently overwrites
authoritative evidence.

## Shared-memory scope enforcement

The shared memory service enforces scope before retrieval or prompt assembly. Private portfolio
facts, goals, chats, traces, preferences and outcomes stay user-scoped and never enter another
user's retrieval or assembled context. Public facts are independently-sourced market knowledge
via the existing operator-only ingestion path. Generic procedures may be shared ONLY after
review: this slice stores the procedural `kind` and its `review_status`, but implements no
automatic promotion or sharing (that is M5b). An un-reviewed procedure can only exist as a
private row, and the extended DB CHECK forbids a `public` + `procedural` + `unreviewed` row, so
it is never retrievable as shared/public.

## Deletion lineage and expiry

`delete_memory(context, memory_id, *, reason)` authorizes ownership, sets `status='deleted'`
and writes a `memory_deletions` lineage row; the body is retained for lineage but excluded from
retrieval and never resurrected. `supersede_memory(context, memory_id, *, reason, kind, body,
source, ...)` marks the old row `superseded`, writes lineage, and inserts the replacement with
incremented `version` and `supersedes` set, all in one transaction. A user cannot delete or
supersede another user's memory. Expired rows need no write — the retrieval filter excludes
`expires_at <= query_time`, and deprecated/superseded/deleted rows are excluded by the
status filter.

## Sixth evaluation suite

`m5a-memory-baseline-v1` is wired into `cli eval` as the sixth suite (`m5a_evaluation.evaluate`,
cases in `m5a_cases.json`, baseline recorded at `evals/baselines/m5a-memory-v1.json`). Seven
synthetic scenarios exercise the ACTUAL services: `scoped_retrieval_excludes_cross_user`,
`status_expiry_filtered_before_rank`, `unreviewed_procedure_not_shared`,
`deterministic_keyword_ranking`, `run_capture_redacts_secret`, `trace_stays_payload_free` and
`deletion_lineage_retained`. The harness is fail-closed — mutating any expected answer drives
`passed` to 0 and changes the dataset hash, so `cli eval` exits nonzero. This is a
SCOPE/CAPTURE/RETRIEVAL correctness gate kept DISTINCT from any held-out learning evaluation;
it carries NO `learning_improvement` key and establishes no adaptation claim.
