# M2a: fixture-backed read-only valuation

## Scope

Value current holdings of supported US stocks, ETFs and mutual funds using operator-ingested, independently sourced observations. This slice has no external provider or model dependency. It preserves M1 authentication, private snapshots and transactional traces. It does not calculate historical portfolio performance, cost basis, returns, tax lots, cash balances or trade fills.

The observation cutoff is `market_as_of`, NOT a historical holdings date. Each report explicitly says `holdings_basis=current_snapshot`, includes the held quantities, and hashes the security/quantity/kind snapshot. Changing a holding changes the next report, while the saved earlier report retains its inputs and result. Import provenance, effective-dated instrument aliases and audited removal/reconciliation remain the next M2 slice.

## Service contracts

- `OperatorService.ingest_observation(context, observation)` requires an operator credential. The instrument must already be validated and registered. The caller supplies a public source, Decimal value, USD currency, basis, observed time and fetch time. Instrument eligibility is inherited from the master; this is not an authoritative validation provider.
- `PortfolioService.value_portfolio(context, portfolio_id, as_of=..., source=..., policy=...)` checks ownership, selects observations and persists a private immutable-by-API result. Success trace and snapshot commit atomically.
- `PortfolioService.valuation_snapshot(context, snapshot_id)` requires both the snapshot's original owner and current portfolio ownership. Transferring a portfolio does not expose old snapshots to its new owner.

Observations are shared public data; snapshots contain private quantities and values. Raw Store/SQLite access remains trusted administration. No update/delete snapshot endpoint exists; direct database access is not a security sandbox.

## Observation rules

Timestamps must be timezone-aware and normalize to UTC. Fetch time cannot precede observation time. Stocks and ETFs require `price`; mutual funds require `nav`. Unknown securities, non-USD data, negative/nonfinite numbers, floats and missing sources are rejected. Zero is a legitimate known value, distinct from missing data. Values and quantities used for valuation support up to 28 coefficient digits, 12 fractional places, and a 19-digit integer magnitude; out-of-range values fail explicitly. Decimal multiplication and addition use a fixed 80-digit local context, independent of ambient precision. No cent rounding is applied in the domain result.

An exact repeated observation returns the same content-derived ID. Conflicting values with the same security/source/observed/fetched/basis identity are rejected. Corrections require a distinct fetch timestamp. That preserves what was known at each cutoff.

Selection uses one explicit source and the appropriate basis. Both observation time and fetch time must be at or before the cutoff. The latest observed time wins, then latest fetch time, then ID. There is no silent fallback to another provider and no use of data fetched after the cutoff.

## Result and freshness semantics

Each line contains quantity, kind, basis, status, unit value, market value, observation ID, source, observed time and fetch time. Missing lines keep numeric values and evidence fields null.

| Report status | Meaning | `total` |
|---|---|---|
| complete | Every holding has an observation within its configured age window | Exact decimal string |
| stale | No missing observations, but at least one exceeds its age window | null |
| incomplete | At least one holding has no eligible observation | null |

`priced_subtotal` sums all available values, INCLUDING stale values; it is never a substitute for a complete total. Missing holdings are excluded from that subtotal rather than assigned a zero price. An empty portfolio returns a complete total of zero.

Default freshness windows are 24 elapsed hours for price and 72 elapsed hours for NAV, inclusive at the boundary. They are explicit fixture defaults, not exchange-calendar or fund-publication rules. The caller may supply a validated FreshnessPolicy, and each snapshot records the windows. Weekend, holiday and provider-specific publication schedules remain future work. A NAV line is always labelled NAV and dated; “fresh” never means intraday executable pricing.

## Local synthetic example

```python
from datetime import datetime, timezone
from decimal import Decimal
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import Store, FixtureValidator, ValidatedSecurity
from portfolio_agent.ports import Observation
from portfolio_agent.services import PortfolioService, OperatorService

store = Store()
try:
    store.create_user('alice')
    store.create_user('operator')
    auth = Authenticator(store)
    user = auth.authenticate(auth.provision('alice'))
    operator = auth.authenticate(auth.provision('operator', role='operator'))
    validator = FixtureValidator({
        'DEMO': ValidatedSecurity('demo', 'DEMO', 'mutual_fund', 'US', 'synthetic')
    })
    app = PortfolioService(store, auth, validator)
    ops = OperatorService(app)
    portfolio = app.create_portfolio(user, 'Synthetic example')
    app.import_holding(user, portfolio, 'DEMO', '3')
    observed = datetime(2026, 11, 5, 21, tzinfo=timezone.utc)
    cutoff = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
    ops.ingest_observation(operator, Observation(
        'demo', Decimal('5.555'), observed, 'fixture', 'nav', fetched_at=cutoff
    ))
    result = app.value_portfolio(user, portfolio, as_of=cutoff, source='fixture')
    assert result['total'] == '16.665'
finally:
    store.close()
```

## Persistence and verification

Schema v3 adds `market_observations` and `valuation_snapshots` without rewriting existing holdings, credentials or memories. M0 and M1 upgrades follow the existing atomic migration path. Backup/restore includes observations and snapshots; restore still revokes credentials. Backup helpers require the current schema. Upgrade older archives on a separate working copy before making a new current-schema backup.

Run `PYTHONPATH=src python -m portfolio_agent.cli eval-valuation` or the installed `portfolio-agent eval-valuation`. Seven public synthetic cases test mixed equity arithmetic, dated NAV, stale data, missing data, late-fetched data, known zero and empty portfolios. Every case must pass; intentional expected-answer mutation must fail. These are regression/correctness evaluations, not held-out evidence of learning or investment results.
