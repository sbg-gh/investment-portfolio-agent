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


def sized_prereg(target=12, equity=4, holdings=2, adversarial=2, contaminated=2):
    """A copy of the pre-registration with smaller family counts (independent fixtures).
    The dataset rule, lag rule, seed and statistics are unchanged; only sizes differ."""
    import copy
    from portfolio_agent.m5b_stats import load_prereg
    prereg = copy.deepcopy(load_prereg()[0])
    prereg['family_counts'] = {
        'fund_value_question': target, 'fund_nav_report': target, 'equity_quote': equity,
        'holdings_list': holdings, 'adversarial_xuser': adversarial, 'contaminated_doc': contaminated}
    prereg['n_target'] = 2 * target
    return prereg


_MAIN_RUN = []


def main_run():
    """The pre-registered analysis, executed ONCE per test process and shared (read-only)."""
    if not _MAIN_RUN:
        from portfolio_agent.m5b_harness import run_ablation
        _MAIN_RUN.append(run_ablation())
    return _MAIN_RUN[0]
