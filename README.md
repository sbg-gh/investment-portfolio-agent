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

## Implemented

- Versioned SQLite schema, scoped portfolio imports, positive decimal quantities, validated instrument registration and a 75-slot global monitoring cap; research target 400 remains soft.
- Local bearer credential provisioning with hashed storage, revocation and expiry; signed five-minute contexts bind identity to the authenticated credential.
- Application services that derive the owner from context; separate operator-gated public ingestion and membership management.
- Explicit private/public memory scopes with database constraints. Missing private owners are rejected. Legacy ownerless memories are quarantined on migration, not published.
- Private execution traces containing operation, outcome, policy version and timing, without portfolio IDs or request payloads. Successful changes and traces commit atomically.
- Consistent backup/restore to a new file, integrity/schema checks and credential revocation on restore.
- Deterministic safety evaluation fixtures and CI gates alongside regression tests.
- Read-only default; explicit paper buy-intent records do not change actual holdings.

- Fixture-backed stock/ETF and dated fund-NAV valuation with explicit stale/missing states, source/cutoff provenance and private saved snapshots.

## Boundaries

M1 implements local identity, persistence and service boundaries. It does not include an HTTP listener, interactive login, UI, prompt/cache authorization adapters or production deployment. Store, credential provisioning and backup functions are trusted operator internals. They are not endpoints. Python code and anyone with database/process access remain trusted; this is not an in-process security sandbox.

Real providers, financial accounting, proactive jobs, local inference, conversational orchestration, learned-procedure promotion and full paper ledgers remain planned. The evaluation baseline measures deterministic safety, not investment performance or behavioral improvement.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/DECISIONS.md)
- [M1 implementation and local usage](docs/M1.md)
- [Domain](docs/specs/DOMAIN.md), [services](docs/specs/SERVICES.md), [memory](docs/specs/MEMORY.md)
- [Read-only valuation and example](docs/VALUATION.md)
- [Testing and evaluation](docs/TESTING.md)
- [Operations](docs/OPERATIONS.md), [milestones](docs/MILESTONES.md), [handoff](docs/HANDOFF.md)
- [Verification](VERIFICATION.md)

No project license has been selected. No third-party source or model weights are bundled.

## First watchlist

[AI Conviction](docs/WATCHLIST.md) contains the 20 user-selected symbols in screenshot order. The seed awaits instrument validation and owner-scoped runtime import.
