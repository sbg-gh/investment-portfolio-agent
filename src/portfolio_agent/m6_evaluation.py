"""Deterministic M6 paper-simulation regression fixtures. Not held-out learning evidence.
Each scenario drives an isolated in-memory Store through the authorized PortfolioService and
observes one paper-simulation correctness property: real holdings untouched, live mode
rejected, overdraft/oversell/stale/missing rejections with no effect, reproducible fills, dated
NAV, exact fees, FIFO cost basis, reconciliation, idempotent retries, cross-user denial and
withheld totals. Securities are obviously synthetic; observations are operator-ingested with
fixed datetimes and there is no randomness.
This is a deterministic correctness gate ONLY. It makes no performance, learning or
investment-return claim and has no 'learning_improvement' or 'held_out_evaluation' signal.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from importlib.resources import files
import json
from .auth import Authenticator
from .core import FixtureValidator, Store, ValidatedSecurity
from .paper import PaperRejected
from .ports import Observation
from .services import OperatorService, PortfolioService, POLICY_VERSION

AS_OF = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
SOURCE = 'synthetic-fixture'
STOCK, ETF, FUND = 'syn:stock', 'syn:etf', 'syn:fund'
_SECURITIES = {'STOCK': (STOCK, 'stock'), 'ETF': (ETF, 'etf'), 'FUND': (FUND, 'mutual_fund')}
_REAL_TABLES = ('holdings', 'transactions', 'import_receipts', 'holding_events', 'baseline_coverage')


def load_cases():
    return json.loads(files('portfolio_agent').joinpath('m6_cases.json').read_text())


class _Env:
    """Isolated store with two synthetic owners, an operator and the services over it."""
    def __init__(self):
        self.store = Store()
        for name in ('fixture-alice', 'fixture-bob', 'fixture-operator'):
            self.store.create_user(name)
        validator = FixtureValidator({
            symbol: ValidatedSecurity(sid, symbol, kind, 'US', 'synthetic')
            for symbol, (sid, kind) in _SECURITIES.items()})
        auth = Authenticator(self.store, clock=lambda: 1000.0)
        self.alice = auth.authenticate(auth.provision('fixture-alice'))
        self.bob = auth.authenticate(auth.provision('fixture-bob'))
        operator = auth.authenticate(auth.provision('fixture-operator', role='operator'))
        self.app = PortfolioService(self.store, auth, validator)
        self._ops, self._operator = OperatorService(self.app), operator
        with self.store.db:
            for record in validator.records.values():
                self.store.register(record)

    def portfolio(self, context=None):
        return self.app.create_portfolio(context or self.alice, 'synthetic')

    def observe(self, sid, value, observed=AS_OF, fetched=None):
        basis = 'nav' if sid == FUND else 'price'
        self._ops.ingest_observation(self._operator, Observation(
            sid, Decimal(value), observed, SOURCE, basis, fetched_at=fetched or observed))

    def account(self, fee_kind='none', fee_amount='0', cash=None, context=None, portfolio=None):
        context = context or self.alice
        account = self.app.create_paper_account(
            context, portfolio or self.portfolio(context), source=SOURCE, fee_kind=fee_kind,
            fee_amount=fee_amount, mode='paper')
        if cash is not None:
            self.deposit(account, cash, key=f'dep-{cash}', context=context)
        return account

    def deposit(self, account, amount, key, context=None):
        return self.app.paper_deposit(context or self.alice, account, amount, source=SOURCE,
                                      idempotency_key=key, as_of=AS_OF, mode='paper')

    def order(self, account, sid, side, quantity, key, as_of=AS_OF, context=None):
        return self.app.submit_paper_order(context or self.alice, account, sid, side, quantity,
                                           as_of=as_of, idempotency_key=key, mode='paper')

    def ledger(self, account):
        return self.app.paper_ledger(self.alice, account)

    def cash(self, account):
        return self.app.paper_positions(self.alice, account)['cash']

    def real_digest(self):
        dump = {table: [list(row) for row in self.store.db.execute(
            f'SELECT * FROM {table} ORDER BY rowid')] for table in _REAL_TABLES}
        return hashlib.sha256(json.dumps(dump, sort_keys=True).encode()).hexdigest()

    def rejected(self, reason, account, sid, side, quantity, key, as_of=AS_OF):
        """True iff the order raised PaperRejected(reason) and left the ledger untouched."""
        before = self.ledger(account)
        try:
            self.order(account, sid, side, quantity, key, as_of)
        except PaperRejected as rejection:
            return rejection.reason == reason and self.ledger(account) == before
        return False

    def close(self):
        self.store.close()


@contextmanager
def _env():
    env = _Env()
    try:
        yield env
    finally:
        env.close()


def _real_holdings_untouched():
    with _env() as env:
        portfolio = env.portfolio()
        env.app.import_holding(env.alice, portfolio, 'STOCK', '7')
        env.app.record_transaction(env.alice, portfolio, STOCK, 'buy', '2', source=SOURCE,
                                   idempotency_key='real-1', occurred_at=AS_OF)
        env.app.import_holding_with_provenance(env.alice, portfolio, 'ETF', '3', source=SOURCE,
                                               market_as_of=AS_OF, idempotency_key='real-2')
        before = env.real_digest()
        for sid in (STOCK, ETF):
            env.observe(sid, '10')
        env.observe(FUND, '20', observed=AS_OF - timedelta(days=1))
        for fee_kind, fee_amount in (('none', '0'), ('flat', '1'), ('per_share', '0.1'),
                                     ('percentage', '0.01')):
            account = env.account(fee_kind, fee_amount, cash='1000', portfolio=portfolio)
            env.order(account, STOCK, 'buy', '5', 'b1')
            env.order(account, STOCK, 'sell', '2', 's1')
            env.order(account, FUND, 'buy', '1', 'b2')
            env.order(account, STOCK, 'buy', '5', 'b1')
            env.rejected('insufficient_position', account, ETF, 'sell', '1', 'x1')
            env.rejected('insufficient_cash', account, ETF, 'buy', '1000', 'x2')
            env.app.value_paper_account(env.alice, account, as_of=AS_OF)
            env.app.reconcile_paper_account(env.alice, account)
        return 'unchanged' if env.real_digest() == before else 'changed'


def _live_mode_rejected():
    with _env() as env:
        portfolio = env.portfolio()
        account = env.account(portfolio=portfolio)
        bad_modes = ('live', 'LIVE', 'read_only', '', None, 'paper ')
        env.observe(STOCK, '10')
        env.deposit(account, '100', key='seed')
        before = (env.ledger(account), env.store.db.execute(
            'SELECT COUNT(*) FROM paper_accounts').fetchone()[0])

        def attempts(**mode):
            yield lambda: env.app.create_paper_account(env.alice, portfolio, source=SOURCE, **mode)
            yield lambda: env.app.paper_deposit(env.alice, account, '1', source=SOURCE,
                                                idempotency_key='m', as_of=AS_OF, **mode)
            yield lambda: env.app.submit_paper_order(env.alice, account, STOCK, 'buy', '1',
                                                     as_of=AS_OF, idempotency_key='m', **mode)
        rejected = True
        for variant in [{'mode': m} for m in bad_modes] + [{}]:
            for attempt in attempts(**variant):
                try:
                    attempt()
                    rejected = False
                except PermissionError:
                    pass
        after = (env.ledger(account), env.store.db.execute(
            'SELECT COUNT(*) FROM paper_accounts').fetchone()[0])
        accepted = env.order(account, STOCK, 'buy', '1', 'ok')['status'] == 'filled'
        return 'all_rejected_none_written' if rejected and before == after and accepted else 'leaked'


def _overdraft_rejected():
    with _env() as env:
        env.observe(STOCK, '10')
        account = env.account('flat', '1', cash='100')
        # 10 x 10 = 100 alone fits the cash; the 1 fee does not.
        ok = env.rejected('insufficient_cash', account, STOCK, 'buy', '10', 'over')
        return 'insufficient_cash_no_effect' if ok and env.cash(account) == '100' else 'leaked'


def _oversell_rejected():
    with _env() as env:
        env.observe(STOCK, '10')
        account = env.account(cash='1000')
        env.order(account, STOCK, 'buy', '2', 'b1')
        ok = env.rejected('insufficient_position', account, STOCK, 'sell', '3', 'over')
        held = env.app.paper_positions(env.alice, account)['positions'][0]['quantity']
        return 'insufficient_position_no_effect' if ok and held == '2' else 'leaked'


def _fill_digest():
    with _env() as env:
        env.observe(STOCK, '12.5')
        account = env.account('per_share', '0.01', cash='1000')
        return env.order(account, STOCK, 'buy', '4', 'b1')


def _stock_fill_reproducible():
    first, second = _fill_digest(), _fill_digest()
    same = all(first[k] == second[k] for k in ('fill_digest', 'unit_price', 'fee', 'cash_delta',
                                                 'observation_id'))
    return 'identical' if same and len(first['fill_digest']) == 64 else 'diverged'


def _post_cutoff_observation_ignored():
    with _env() as env:
        env.observe(STOCK, '10', observed=AS_OF - timedelta(hours=1))
        env.observe(STOCK, '99', observed=AS_OF + timedelta(hours=1))
        # Observed before the cutoff but fetched after it: not yet known at the cutoff.
        env.observe(STOCK, '98', observed=AS_OF - timedelta(minutes=30),
                    fetched=AS_OF + timedelta(minutes=5))
        account = env.account(cash='1000')
        fill = env.order(account, STOCK, 'buy', '1', 'b1')
        return 'prior_observation_used' if fill['unit_price'] == '10' else 'future_data_used'


def _stale_price_rejected():
    with _env() as env:
        env.observe(STOCK, '10', observed=AS_OF - timedelta(days=2))
        account = env.account(cash='1000')
        ok = env.rejected('price_stale', account, STOCK, 'buy', '1', 'b1')
        return 'price_stale_no_effect' if ok and env.cash(account) == '1000' else 'leaked'


def _missing_price_rejected():
    with _env() as env:
        account = env.account(cash='1000')
        ok = env.rejected('price_missing', account, STOCK, 'buy', '1', 'b1')
        return 'price_missing_no_effect' if ok and env.cash(account) == '1000' else 'leaked'


def _fund_fills_at_dated_nav():
    with _env() as env:
        published = AS_OF - timedelta(days=1)
        env.observe(FUND, '20', observed=published)
        account = env.account(cash='1000')
        fill = env.order(account, FUND, 'buy', '2', 'b1')
        dated = fill['basis'] == 'nav' and fill['nav_date'] == published.date().isoformat()
        return 'nav_dated' if dated and fill['unit_price'] == '20' else 'not_nav'


def _stale_nav_rejected():
    with _env() as env:
        env.observe(FUND, '20', observed=AS_OF - timedelta(days=4))
        account = env.account(cash='1000')
        ok = env.rejected('nav_stale', account, FUND, 'buy', '1', 'b1')
        return 'nav_stale_no_effect' if ok and env.cash(account) == '1000' else 'leaked'


def _fee_models_exact():
    with _env() as env:
        env.observe(STOCK, '20')
        # quantity 3 at 20 => gross 60; hand-computed fees, exact and unrounded.
        expected = {('none', '0'): '0', ('flat', '1.5'): '1.5', ('per_share', '0.25'): '0.75',
                    ('percentage', '0.001'): '0.06'}
        for (kind, amount), fee in expected.items():
            account = env.account(kind, amount, cash='1000')
            fill = env.order(account, STOCK, 'buy', '3', 'b1')
            cash_delta = '-' + str(Decimal('60') + Decimal(fee))
            if fill['fee'] != fee or fill['gross'] != '60' or fill['cash_delta'] != cash_delta:
                return 'inexact'
        return 'exact'


def _fifo_cost_basis_exact():
    with _env() as env:
        account = env.account(cash='10000')
        steps = (('buy', '10', '10', 3, 'b1'), ('buy', '10', '12', 2, 'b2'), ('sell', '15', '15', 1, 's1'))
        for side, quantity, price, hours_before, key in steps:
            when = AS_OF - timedelta(hours=hours_before)
            env.observe(STOCK, price, observed=when)
            env.order(account, STOCK, side, quantity, key, as_of=when)
        state = env.app.paper_positions(env.alice, account)
        position = state['positions'][0]
        # Lot 1 (10 @ 10 = 100) is consumed whole, 5 of lot 2 (basis 120) cost 60: proceeds 225
        # less 160 basis is 65 realized; 5 shares with basis 60 remain.
        exact = (state['realized_pnl'], position['quantity'], position['cost_basis'],
                 state['cash']) == ('65', '5', '60', '10005')
        return 'fifo_exact' if exact else 'inexact'


def _reconciled_account(env):
    env.observe(STOCK, '10')
    env.observe(FUND, '20', observed=AS_OF - timedelta(days=1))
    account = env.account('percentage', '0.01', cash='5000')
    env.order(account, STOCK, 'buy', '10', 'b1')
    env.order(account, FUND, 'buy', '2', 'b2')
    env.order(account, STOCK, 'sell', '4', 's1')
    return account


def _ledger_reconciles():
    with _env() as env:
        account = _reconciled_account(env)
        rows = env.app.reconcile_paper_account(env.alice, account)
        return 'reconciled' if rows and all(r['ok'] for r in rows) else 'diverged'


def _tampered_ledger_flagged():
    with _env() as env:
        account = _reconciled_account(env)
        # Isolated store: drop the append-only trigger to simulate out-of-band tampering.
        env.store.db.execute('DROP TRIGGER paper_ledger_no_update')
        env.store.db.execute("UPDATE paper_ledger SET cash_delta='-1' WHERE idempotency_key='b1'")
        rows = env.app.reconcile_paper_account(env.alice, account)
        cash = next(r for r in rows if r['check'] == 'cash')
        kept = env.store.db.execute(
            "SELECT cash_delta FROM paper_ledger WHERE idempotency_key='b1'").fetchone()[0]
        flagged = not cash['ok'] and cash['ledger_cash_sum'] != cash['recomputed_cash_from_components']
        return 'divergence_flagged' if flagged and kept == '-1' else 'coerced'


def _idempotent_retry_single_fill():
    with _env() as env:
        env.observe(STOCK, '10')
        account = env.account(cash='1000')
        first = env.order(account, STOCK, 'buy', '1', 'same-key')
        second = env.order(account, STOCK, 'buy', '1', 'same-key')
        fills = [e for e in env.ledger(account) if e['entry_type'] == 'buy']
        one = (len(fills) == 1 and first['entry_id'] == second['entry_id']
               and second['replayed'] and not first['replayed'] and env.cash(account) == '990')
        return 'one_fill' if one else 'duplicated'


def _cross_user_denied():
    with _env() as env:
        env.observe(STOCK, '10')
        portfolio = env.portfolio()
        account = env.account(cash='1000', portfolio=portfolio)
        env.order(account, STOCK, 'buy', '1', 'b1')
        before = (env.ledger(account), env.store.db.execute(
            'SELECT COUNT(*) FROM paper_accounts').fetchone()[0])
        bob, app = env.bob, env.app
        attempts = (
            lambda: app.create_paper_account(bob, portfolio, source=SOURCE, mode='paper'),
            lambda: app.paper_deposit(bob, account, '5', source=SOURCE, idempotency_key='x',
                                      as_of=AS_OF, mode='paper'),
            lambda: app.submit_paper_order(bob, account, STOCK, 'buy', '1', as_of=AS_OF,
                                           idempotency_key='x', mode='paper'),
            lambda: app.paper_ledger(bob, account),
            lambda: app.paper_positions(bob, account),
            lambda: app.value_paper_account(bob, account, as_of=AS_OF),
            lambda: app.reconcile_paper_account(bob, account),
        )
        for attempt in attempts:
            try:
                attempt()
                return 'leaked'
            except PermissionError:
                pass
        after = (env.ledger(account), env.store.db.execute(
            'SELECT COUNT(*) FROM paper_accounts').fetchone()[0])
        return 'denied_no_effect' if before == after else 'leaked'


def _valuation_incomplete_when_stale():
    with _env() as env:
        env.observe(STOCK, '10')
        account = env.account(cash='1000')
        env.order(account, STOCK, 'buy', '2', 'b1')
        fresh = env.app.value_paper_account(env.alice, account, as_of=AS_OF)
        stale = env.app.value_paper_account(env.alice, account, as_of=AS_OF + timedelta(days=2))
        ok = (fresh['status'] == 'complete' and fresh['total'] == '1000'
              and stale['status'] == 'stale' and stale['total'] is None
              and stale['priced_subtotal'] == '20')
        return 'total_withheld' if ok else 'fabricated'


_RUNNERS = {
    'real_holdings_untouched': _real_holdings_untouched,
    'live_mode_rejected': _live_mode_rejected,
    'overdraft_rejected': _overdraft_rejected,
    'oversell_rejected': _oversell_rejected,
    'stock_fill_reproducible': _stock_fill_reproducible,
    'post_cutoff_observation_ignored': _post_cutoff_observation_ignored,
    'stale_price_rejected': _stale_price_rejected,
    'missing_price_rejected': _missing_price_rejected,
    'fund_fills_at_dated_nav': _fund_fills_at_dated_nav,
    'stale_nav_rejected': _stale_nav_rejected,
    'fee_models_exact': _fee_models_exact,
    'fifo_cost_basis_exact': _fifo_cost_basis_exact,
    'ledger_reconciles': _ledger_reconciles,
    'tampered_ledger_flagged': _tampered_ledger_flagged,
    'idempotent_retry_single_fill': _idempotent_retry_single_fill,
    'cross_user_denied': _cross_user_denied,
    'valuation_incomplete_when_stale': _valuation_incomplete_when_stale,
}
SCENARIOS = set(_RUNNERS)


def _scenario(name):
    return _RUNNERS[name]() if name in _RUNNERS else 'unknown'


def evaluate(cases=None):
    cases = load_cases() if cases is None else cases
    if not cases or len({case['id'] for case in cases}) != len(cases):
        raise ValueError('Evaluation cases must be nonempty with unique IDs')
    if any(case['scenario'] not in SCENARIOS for case in cases):
        raise ValueError('Unknown evaluation scenario')
    results = []
    for case in cases:
        observed = _scenario(case['scenario'])
        results.append({'id': case['id'], 'expected': case['expected'], 'observed': observed,
                        'passed': observed == case['expected']})
    return {'suite': 'm6-paper-baseline-v1', 'policy_version': POLICY_VERSION,
            'dataset_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            'total': len(results), 'passed': sum(r['passed'] for r in results), 'results': results}
