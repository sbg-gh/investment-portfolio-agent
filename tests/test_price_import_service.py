"""OperatorService.import_prices: operator-only, one transaction, one metadata-only trace.
Synthetic fixtures only."""
from datetime import datetime, timezone
import unittest
from unittest import mock

from portfolio_agent import price_import, valuation
from portfolio_agent.auth import AuthContext, Authenticator
from portfolio_agent.core import Store, ValidatedSecurity
from portfolio_agent.registry_validator import RegistryValidator
from portfolio_agent.services import OperatorService, PortfolioService

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
CSV = 'symbol,date,close\nSYNA,2026-11-05,1.5\nSYNB,2026-11-05,2\n'


class ImportServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)
        for sid, symbol in (('s1', 'SYNA'), ('s2', 'SYNB')):
            self.store.register(ValidatedSecurity(sid, symbol, 'stock', 'US', 'synthetic-test'))
        for user in ('alice', 'admin'):
            self.store.create_user(user)
        self.auth = Authenticator(self.store)
        self.ctx = {u: self.auth.authenticate(self.auth.provision(
            u, role='operator' if u == 'admin' else 'user')) for u in ('alice', 'admin')}
        self.app = PortfolioService(self.store, self.auth, RegistryValidator(self.store))
        self.ops = OperatorService(self.app)

    def count(self, table):
        return self.store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def trace(self):
        return self.store.db.execute(
            "SELECT operation,outcome FROM execution_traces WHERE operation='import_prices'").fetchall()

    def test_non_operator_is_denied_and_writes_nothing(self):
        with self.assertRaises(PermissionError):
            self.ops.import_prices(self.ctx['alice'], source='s', csv_text=CSV, dry_run=False, now=NOW)
        self.assertEqual(self.count('market_observations'), 0)

    def test_unauthenticated_is_denied(self):
        with self.assertRaises(PermissionError):
            self.ops.import_prices(AuthContext('nobody', 'cred', 'req', 0.0, 'bad'), source='s', csv_text=CSV,
                                   dry_run=False, now=NOW)
        self.assertEqual(self.count('market_observations'), 0)

    def test_operator_dry_run_writes_no_observations(self):
        result = self.ops.import_prices(self.ctx['admin'], source='s', csv_text=CSV, dry_run=True, now=NOW)
        self.assertEqual(result['counts'], {'imported': 2})
        self.assertEqual(self.count('market_observations'), 0)

    def test_operator_import_writes_and_records_one_trace(self):
        result = self.ops.import_prices(self.ctx['admin'], source='s', csv_text=CSV, dry_run=False, now=NOW)
        self.assertEqual(result['counts'], {'imported': 2})
        self.assertEqual(self.count('market_observations'), 2)
        self.assertEqual(self.trace(), [('import_prices', 'success')])
        self.assertEqual(self.app.observation_sources(self.ctx['alice']), ['s'])

    def test_file_error_is_recorded_as_invalid_and_writes_nothing(self):
        with self.assertRaises(price_import.FileError) as caught:
            self.ops.import_prices(self.ctx['admin'], source='s', csv_text='a,b\n1,2\n', dry_run=False, now=NOW)
        self.assertEqual(caught.exception.code, 'invalid_header')
        self.assertEqual(self.trace(), [('import_prices', 'invalid')])
        self.assertEqual(self.count('market_observations'), 0)

    def test_failure_rolls_back_but_trace_is_kept(self):
        calls = []
        real = valuation.ingest_observation

        def flaky(store, observation):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('boom')
            return real(store, observation)
        with mock.patch.object(valuation, 'ingest_observation', flaky):
            with self.assertRaises(RuntimeError):
                self.ops.import_prices(self.ctx['admin'], source='s', csv_text=CSV, dry_run=False, now=NOW)
        self.assertEqual(self.count('market_observations'), 0)
        self.assertEqual(self.trace(), [('import_prices', 'error')])


if __name__ == '__main__':
    unittest.main()
