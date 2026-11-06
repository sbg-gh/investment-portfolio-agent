# Development contract

Build a vendor-neutral, local-first Investment Portfolio Management Agent for personal US stocks, ETFs and mutual funds, with private portfolios for multiple users. Read docs/DECISIONS.md, docs/ARCHITECTURE.md and docs/MILESTONES.md before changing behavior.

## Non-negotiable invariants
- Derive user identity from a trusted authentication context at future API boundaries. Never trust a user ID supplied by chat, a model, request body or scheduled-job payload alone.
- Authorize portfolio operations before reads, writes, retrieval, model prompting, caching and notification. One user's holdings, preferences and experiences must not become another user's context.
- Share public market knowledge and reviewed generic procedures only. Shared memory means a common service with explicit scopes, not unrestricted access.
- Research universe target is 400; enhanced monitoring has 75 distinct global security slots. Neither limits valid holdings or security-master size. All held securities retain baseline coverage.
- Validate held instruments through an authoritative adapter before auto-registration. Unknown/ambiguous instruments require reconciliation. Never invent identifiers or prices.
- Keep orchestration, deterministic finance, providers, model inference, memory, experience evaluation and notifications modular. Model output is untrusted input.
- No broker credentials, live execution adapters or live order mode. Paper records must never mutate real holdings.
- Keep runtime local and lightweight; bound inference concurrency and context. Avoid required cloud services and model retraining.

## Implementation practice
M1 supplies local authenticated application services, not a network multi-user server. Store and credential provisioning remain trusted internals. Follow milestone acceptance gates before exposing interfaces. Keep fixtures obviously synthetic. Use Decimal for quantities and money; UTC timestamps with provenance in new observation adapters. Do not log private context by default. Schema changes after M0 need versioned migrations and backup/restore tests.

Run `PYTHONPATH=src python -m unittest discover -s tests -v` and `PYTHONPATH=src python -m portfolio_agent.cli demo`. Add meaningful isolation and failure-path tests for new features. Update contracts and decisions alongside code. Do not claim experience learning from storage or retrieval alone: policy changes need held-out evaluation and rollback.

## Test-driven development
For every behavior change, write a meaningful failing test before implementation, then make it pass and refactor. Include negative authorization, malformed scope, migration rollback and recovery tests where relevant. Run the deterministic evaluation gate (`PYTHONPATH=src python -m portfolio_agent.cli eval`) alongside the unit tests and demo. Keep safety fixtures distinct from held-out learning evaluation and never claim adaptation from passing regression checks. See docs/TESTING.md.

For valuation changes, also run `PYTHONPATH=src python -m portfolio_agent.cli eval-valuation`. Preserve current-holdings versus historical-market-cutoff semantics, dated NAV, explicit stale/missing values and snapshot isolation. See docs/VALUATION.md.
