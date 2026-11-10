"""Synthetic fixtures shared by the M6 paper-simulation tests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from portfolio_agent import paper
from portfolio_agent.core import Store, ValidatedSecurity
from portfolio_agent.ports import Observation
from portfolio_agent.valuation import ingest_observation

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
SOURCE = 'fixture'


def make_store(path=':memory:'):
    s = Store(path)
    for user in ('alice', 'bob'):
        s.create_user(user)
    for sym, kind in (('STOCK', 'stock'), ('ETF', 'etf'), ('FUND', 'mutual_fund')):
        s.register(ValidatedSecurity(sym, sym, kind, 'US', 'synthetic'))
    s.db.commit()
    return s


def observe(store, sym, value, *, age=timedelta(0), fetched_at=None, source=SOURCE, at=NOW):
    basis = 'nav' if sym == 'FUND' else 'price'
    return ingest_observation(store, Observation(sym, Decimal(value), at - age, source, basis,
                                                 fetched_at=fetched_at or (at - age)))


def open_account(store, user='alice', fee=None, source=SOURCE, cash=None):
    portfolio = store.create_portfolio(user, 'private portfolio')
    account = paper.create_account(store, user, portfolio, source=source,
                                   fee_model=fee or paper.FeeModel('none', Decimal(0)), mode='paper')
    if cash is not None:
        deposit(store, user, account, cash)
    return account


def deposit(store, user, account, amount, key=None, as_of=NOW):
    key = key or f'dep-{amount}-{len(paper.ledger(store, user, account))}'
    return paper.deposit(store, user, account, amount, source='fixture', idempotency_key=key,
                         as_of=as_of, mode='paper')


def order(store, user, account, sym, side, qty, key, as_of=NOW, **kwargs):
    return paper.submit_order(store, user, account, sym, side, qty, as_of=as_of,
                              idempotency_key=key, mode='paper', **kwargs)
