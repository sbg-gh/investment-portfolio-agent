# Investment Portfolio Management Agent

Local-first, vendor-neutral foundation for a hybrid conversational and proactive portfolio assistant. Intended for an Apple Silicon Mac mini and portable to Linux. Scope: private multi-user portfolios of US stocks, ETFs and mutual funds; read-only analysis and explicit paper simulation.

## Start offline (Python 3.11+)

```sh
python3 -m venv .venv
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m portfolio_agent.cli demo
PYTHONPATH=src python -m portfolio_agent.cli eval
PYTHONPATH=src python -m portfolio_agent.cli eval-valuation
```

Optional installation (build dependencies may require network):

```sh
python -m pip install -e .
portfolio-agent demo
portfolio-agent eval
portfolio-agent eval-valuation
```

The commands use synthetic data and fresh in-memory databases. No API keys, market-data subscriptions, model downloads or brokerage access are required. The eval command exits nonzero on any failed case. `.env.example` is a future adapter contract, not active configuration.

## Local web UI (optional)

```sh
pip install -e '.[ui]'                                   # uvicorn, h11, click (exact pins; hashed lock in requirements/ui.lock)
npm --prefix ui ci && npm --prefix ui run build          # Node 18.20.2+
python -m portfolio_agent.web create-user alice --db var/portfolio.db
python -m portfolio_agent.web issue-credential alice --db var/portfolio.db --role operator
python -m portfolio_agent.web seed-starter-data --db var/portfolio.db --user alice   # optional: 20 watchlist instruments + a synthetic sample portfolio
python -m portfolio_agent.web import-prices --db var/portfolio.db --csv prices.csv --source my-prices --dry-run   # your own CSV: symbol,date,close
python -m portfolio_agent.web fetch-prices --db var/portfolio.db --dry-run   # optional, unofficial Yahoo end-of-day closes; manual; see docs/DECISIONS.md and docs/UI.md
python -m portfolio_agent.web serve --db var/portfolio.db   # open http://127.0.0.1:8765 and paste the token
```
The starter instruments are operator-attested, not validated; SKHY is the user's own attestation; no prices are seeded (enter them on the Operator screen). The same two actions are buttons on the Operator and Portfolios screens.

Loopback only, no TLS, operator-entered prices, slice 7a screens. See [docs/UI.md](docs/UI.md) for the threat model, limits and the recorded smoke tests. The four gates above need none of this.

## Implemented

- Versioned SQLite schema, scoped portfolio imports, positive decimal quantities, validated instrument registration and a 75-slot global monitoring cap; research target 400 remains soft.
- Local bearer credential provisioning with hashed storage, revocation and expiry; signed five-minute contexts bind identity to the authenticated credential.
- Application services that derive the owner from context; separate operator-gated public ingestion and membership management.
- Explicit private/public memory scopes with database constraints. Missing private owners are rejected. Legacy ownerless memories are quarantined on migration, not published.
- Private execution traces containing operation, outcome, policy version and timing, without portfolio IDs or request payloads. Successful changes and traces commit atomically.
- Consistent backup/restore to a new file, integrity/schema checks and credential revocation on restore.
- Deterministic safety evaluation fixtures and CI gates alongside regression tests.
- Read-only default. The M6 paper ledger (explicit `paper` mode only) simulates cash, fees, FIFO lots and reproducible fills in separate tables; it never reads or writes real holdings. M0 paper buy-intent records remain a reference-price annotation.

- Fixture-backed stock/ETF and dated fund-NAV valuation with explicit stale/missing states, source/cutoff provenance and private saved snapshots.

- Durable, deterministic job store (leases, bounded retries, dead-letter), a persisted notification outbox with per-user preferences and dedup, and minimal monitoring rules (stale-coverage + materiality) that stay quiet on unchanged inputs and re-authorize ownership at execution. A test-invoked runner, no background thread and an in-memory delivery sink.

- Authenticated offline chat over the common services with a bounded, vendor-neutral local-model adapter: owner-scoped context within a token budget, evidence rendering that reuses M2a stale/missing/as-of semantics (never fabricated), and READ-ONLY specialist tools behind a deterministic schema+authorization gate. Model output is untrusted — it only proposes; deterministic code authorizes and executes, so prompt injection cannot grant access or actions.

## Boundaries

M1 implements local identity, persistence and service boundaries. The optional slice-7a web UI adds a loopback HTTP listener with token login and in-memory sessions (docs/UI.md); there are still no prompt/cache authorization adapters or production deployment. Store, credential provisioning and backup functions are trusted operator internals. They are not endpoints. Python code and anyone with database/process access remain trusted; this is not an in-process security sandbox.

Real market-data providers are not implemented. Valuation, proactive jobs, local inference, conversational orchestration, learned-procedure promotion and the paper ledger exist as local, offline building blocks over fixtures; corporate actions, tax-lot reporting, slice 7b of the UI and a real market-data adapter remain outside the delivered plan. The evaluation baseline measures deterministic safety, not investment performance or behavioral improvement.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/DECISIONS.md)
- [M1 implementation and local usage](docs/M1.md)
- [M2 import provenance and reconciliation](docs/M2.md)
- [M3 proactive service](docs/M3.md)
- [M4 hybrid chat and local model](docs/M4.md)
- [M5a memory substrate](docs/M5a.md)
- [M5b experience learning and held-out evaluation](docs/M5b.md)
- [M6 paper portfolio simulation](docs/M6.md)
- [Local web UI](docs/UI.md)
- [Domain](docs/specs/DOMAIN.md), [services](docs/specs/SERVICES.md), [memory](docs/specs/MEMORY.md)
- [Read-only valuation and example](docs/VALUATION.md)
- [Testing and evaluation](docs/TESTING.md)
- [Operations](docs/OPERATIONS.md), [milestones](docs/MILESTONES.md), [handoff](docs/HANDOFF.md)
- [Verification](VERIFICATION.md)

No project license has been selected. No third-party source or model weights are bundled.

## First watchlist

[AI Conviction](docs/WATCHLIST.md) contains the 20 user-selected symbols in screenshot order. The seed awaits instrument validation and owner-scoped runtime import.
