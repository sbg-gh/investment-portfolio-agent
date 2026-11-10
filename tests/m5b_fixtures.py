"""Shared synthetic fixtures for M5b extraction/lifecycle tests (obviously synthetic data)."""
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import FixtureValidator, Store
from portfolio_agent.services import OperatorService, PortfolioService

MF = 'valuation:mutual_fund'
ETF = 'valuation:etf'


def make_world():
    store = Store()
    for user in ('alice', 'bob', 'operator'):
        store.create_user(user)
    auth = Authenticator(store)
    contexts = {
        'alice': auth.authenticate(auth.provision('alice')),
        'bob': auth.authenticate(auth.provision('bob')),
        'operator': auth.authenticate(auth.provision('operator', role='operator')),
    }
    service = PortfolioService(store, auth, FixtureValidator({}))
    return store, auth, service, OperatorService(service), contexts


def seed_runs(store, user, task_type, tool, outcome, count, feedback=None):
    ids = []
    for _ in range(count):
        ids.append(store.record_run(user, request_id='req-fixture', task_type=task_type,
                                    policy_version='m1-v1', tool_sequence=[tool],
                                    outcome=outcome, feedback=feedback))
    return ids


def seed_training(store, user, *, stale=6, nav=5, etf=6, feedback=None):
    """Pre-registered training mix: stale intraday MF runs, successful dated-NAV MF runs,
    successful intraday ETF runs."""
    seed_runs(store, user, MF, 'intraday_quote', 'stale_data', stale, feedback)
    seed_runs(store, user, MF, 'dated_nav', 'success', nav, feedback)
    seed_runs(store, user, ETF, 'intraday_quote', 'success', etf)
