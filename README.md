# Investment Portfolio Management Agent

Repository initialization package for a hybrid conversational and proactive portfolio assistant. Local-first, vendor-neutral, designed for an Apple Silicon Mac mini and portable to Linux. Initial scope: private multi-user portfolios of US stocks, ETFs and mutual funds; read-only analysis and explicit paper simulation.

## Start offline (Python 3.11+)

```sh
python3 -m venv .venv
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m portfolio_agent.cli demo
```

Optional installation (build dependencies may require network):

```sh
python -m pip install -e .
portfolio-agent demo
```

The demo uses a synthetic instrument and a fresh in-memory database. No API keys, live market data, model download, brokerage access or external services are required. For persistence, pass a writable SQLite path to `Store(path)` after creating its parent directory. `.env.example` documents future adapter configuration; it is not loaded by M0.

## Implemented in M0

- SQLite schema and scoped portfolio reads/imports with positive decimal quantities.
- Validated instrument registration, explicit research membership, global 75-slot enhanced monitoring enforcement.
- Private episodic/semantic/procedural memory and separate trusted public-fact ingestion.
- Read-only default and explicit buy-side paper-intent records that never change real holdings.
- Provider protocols, offline CLI demonstration, regression tests and macOS/Linux CI.

## Planned, not implemented

Authenticated chat/API, background scheduler, real validation/data adapters, valuation and performance accounting, model runtime integration, semantic retrieval, notification delivery, full paper ledger, experience extraction and evaluated procedural promotion. M0 does not demonstrate behavioral improvement. See milestone acceptance criteria before expanding exposure.

This foundation trusts the calling process. SQLite and public ingestion methods are operator interfaces, not user endpoints. Do not deploy it as a network multi-user service until M1 supplies authentication, authorized repositories and isolation coverage.

## Architecture map

- [Architecture](docs/ARCHITECTURE.md): components, flows and candidate specialist roles.
- [Decision register](docs/DECISIONS.md): confirmed requirements and explicit assumptions.
- [Domain specification](docs/specs/DOMAIN.md): identities, holdings and universe rules.
- [Service contracts](docs/specs/SERVICES.md): chat, jobs, providers and paper simulation.
- [Memory and evaluation](docs/specs/MEMORY.md): scoped experience learning and experiments.
- [Security and operations](docs/OPERATIONS.md): trust boundaries, local deployment and recovery.
- [Milestones](docs/MILESTONES.md): ordered implementation and acceptance gates.
- [Handoff](docs/HANDOFF.md): first development task.
- [Verification](VERIFICATION.md): package validation results.

No project license has been selected. Choose a license before public distribution. No third-party source code or model weights are bundled.
