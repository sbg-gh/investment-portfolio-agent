"""UI read services and operator instrument registration (no schema change)."""
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from portfolio_agent.auth import Authenticator
from portfolio_agent.core import Store
from portfolio_agent.ports import Observation
from portfolio_agent.registry_validator import RegistryValidator
from portfolio_agent.services import OperatorService, PortfolioService

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


class UiReadTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)
        for user in ('alice', 'bob', 'admin'):
            self.store.create_user(user)
        self.auth = Authenticator(self.store)
        self.ctx = {u: self.auth.authenticate(self.auth.provision(
            u, role='operator' if u == 'admin' else 'user')) for u in ('alice', 'bob', 'admin')}
        self.app = PortfolioService(self.store, self.auth, RegistryValidator(self.store))
        self.ops = OperatorService(self.app)

    def register(self, security_id='sec-demo', symbol='demo', kind='etf'):
        return self.ops.register_instrument(self.ctx['admin'], security_id=security_id,
                                            symbol=symbol, kind=kind)

    # portfolios
    def test_portfolios_lists_only_the_callers(self):
        a = self.app.create_portfolio(self.ctx['alice'], 'alice p')
        self.app.create_portfolio(self.ctx['bob'], 'bob p')
        listing = self.app.portfolios(self.ctx['alice'])
        self.assertEqual(listing, [{'id': a, 'name': 'alice p'}])
        self.assertEqual(self.app.portfolios(self.ctx['admin']), [])

    def test_portfolios_requires_valid_context(self):
        from dataclasses import replace
        with self.assertRaises(PermissionError):
            self.app.portfolios(replace(self.ctx['alice'], signature='bad'))

    # registration
    def test_register_instrument_is_operator_only(self):
        with self.assertRaises(PermissionError):
            self.ops.register_instrument(self.ctx['alice'], security_id='s', symbol='X', kind='etf')

    def test_register_instrument_records_operator_attested_source_and_upper_symbol(self):
        self.assertEqual(self.register(), 'sec-demo')
        row = self.store.db.execute('SELECT symbol,kind,country,source FROM securities WHERE id=?',
                                    ('sec-demo',)).fetchone()
        self.assertEqual(row, ('DEMO', 'etf', 'US', 'operator-attested'))

    def test_register_instrument_is_idempotent_and_rejects_conflicts(self):
        self.register()
        self.register()
        with self.assertRaises(ValueError):
            self.register(kind='stock')
        with self.assertRaises(ValueError):
            self.register(security_id='other')  # same symbol, different identity

    def test_register_instrument_rejects_bad_input(self):
        for kwargs in ({'kind': 'crypto'}, {'symbol': ' '}, {'security_id': ''},
                       {'symbol': 'A' * 40}, {'security_id': 'x' * 200}, {'symbol': 'a b'}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                self.register(**kwargs)

    # validator
    def test_registry_validator_resolves_only_registered_symbols(self):
        self.register()
        pid = self.app.create_portfolio(self.ctx['alice'], 'p')
        self.assertEqual(self.app.import_holding(self.ctx['alice'], pid, ' demo ', '2'), 'sec-demo')
        with self.assertRaises(ValueError):
            self.app.import_holding(self.ctx['alice'], pid, 'NOPE', '2')

    # securities
    def test_securities_returns_public_identity_for_requested_ids(self):
        self.register()
        got = self.app.securities(self.ctx['alice'], ['sec-demo', 'missing'])
        self.assertEqual(got, [{'security_id': 'sec-demo', 'symbol': 'DEMO', 'kind': 'etf'}])

    def test_securities_requires_auth_and_bounds_input(self):
        from dataclasses import replace
        with self.assertRaises(PermissionError):
            self.app.securities(replace(self.ctx['alice'], signature='bad'), [])
        with self.assertRaises(ValueError):
            self.app.securities(self.ctx['alice'], ['x'] * 501)
        with self.assertRaises(ValueError):
            self.app.securities(self.ctx['alice'], [1])

    # sources
    def test_observation_sources_lists_distinct_sorted_sources(self):
        self.register()
        for source, minute in (('zeta', 1), ('alpha', 2), ('alpha', 3)):
            at = NOW.replace(minute=minute)
            self.ops.ingest_observation(self.ctx['admin'], Observation(
                'sec-demo', Decimal('10'), at, source, 'price', fetched_at=at))
        self.assertEqual(self.app.observation_sources(self.ctx['alice']), ['alpha', 'zeta'])


if __name__ == '__main__':
    unittest.main()
