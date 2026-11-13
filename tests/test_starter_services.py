"""Starter-data services: operator-only instrument registration and the user's sample portfolio."""
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from portfolio_agent import starter_data
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import Store
from portfolio_agent.registry_validator import RegistryValidator
from portfolio_agent.services import OperatorService, PortfolioService
NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
NAME = starter_data.SAMPLE_PORTFOLIO_NAME
class StarterServiceTests(unittest.TestCase):
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
    def count(self, table):
        return self.store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    def seed(self):
        return self.ops.register_starter_instruments(self.ctx['admin'])
    # instruments
    def test_registration_is_operator_only(self):
        with self.assertRaises(PermissionError):
            self.ops.register_starter_instruments(self.ctx['alice'])
        self.assertEqual(self.count('securities'), 0)
    def test_registers_twenty_operator_attested_instruments(self):
        self.assertEqual(self.seed(), {'registered': 20, 'already_present': 0, 'symbols': 20})
        rows = self.store.db.execute(
            'SELECT id,symbol,kind,country,source FROM securities ORDER BY rowid').fetchall()
        self.assertEqual(len(rows), 20)
        amd = next(r for r in rows if r[1] == 'AMD')
        self.assertEqual(amd, ('op:amd', 'AMD', 'stock', 'US', 'operator-attested:ai-conviction'))
        skhy = next(r for r in rows if r[1] == 'SKHY')
        self.assertEqual(skhy[4], 'operator-attested:ai-conviction:user-attested')
    def test_registration_is_idempotent(self):
        self.seed()
        self.assertEqual(self.seed(), {'registered': 0, 'already_present': 20, 'symbols': 20})
        self.assertEqual(self.count('securities'), 20)
    def test_hand_registered_symbol_is_left_unchanged_without_conflict(self):
        self.ops.register_instrument(self.ctx['admin'], security_id='my-amd', symbol='AMD', kind='stock')
        self.assertEqual(self.seed(), {'registered': 19, 'already_present': 1, 'symbols': 20})
        self.assertEqual(self.store.db.execute(
            "SELECT id,source FROM securities WHERE symbol='AMD'").fetchall(),
            [('my-amd', 'operator-attested')])
    def test_id_collision_with_other_symbol_is_not_overwritten(self):
        self.ops.register_instrument(self.ctx['admin'], security_id='op:amd', symbol='ZZZ', kind='stock')
        with self.assertRaises(ValueError):
            self.seed()
        self.assertEqual(self.count('securities'), 1, 'failed seed must roll back')
    def test_no_market_observations_are_written(self):
        self.seed()
        self.assertEqual(self.count('market_observations'), 0)
    def test_trace_is_metadata_only(self):
        self.seed()
        row = self.store.db.execute(
            "SELECT operation,outcome FROM execution_traces WHERE operation='register_starter_instruments'"
        ).fetchone()
        self.assertEqual(row, ('register_starter_instruments', 'success'))
    # sample portfolio
    def sample(self, who='alice'):
        return self.app.create_sample_portfolio(self.ctx[who], as_of=NOW)
    def test_sample_needs_registered_instruments_and_creates_nothing(self):
        with self.assertRaises(starter_data.StarterDataMissing):
            self.sample()
        self.assertEqual(self.count('portfolios'), 0)
        self.assertEqual(self.count('import_receipts'), 0)
        self.assertEqual(self.count('holdings'), 0)
    def test_partial_registration_creates_nothing(self):
        self.ops.register_instrument(self.ctx['admin'], security_id='op:amd', symbol='AMD', kind='stock')
        with self.assertRaises(starter_data.StarterDataMissing):
            self.sample()
        self.assertEqual(self.count('portfolios'), 0)
    def test_sample_goes_through_the_import_path_with_receipts(self):
        self.seed()
        result = self.sample()
        self.assertTrue(result['created'])
        self.assertEqual(result['name'], NAME)
        pid = result['id']
        holdings = self.app.holdings(self.ctx['alice'], pid)
        self.assertEqual(len(holdings), 20)
        self.assertTrue(all(Decimal(q) == Decimal(100) and isinstance(q, str) for _, q in holdings))
        receipts = self.app.receipts(self.ctx['alice'], pid)
        self.assertEqual(len(receipts), 20)
        self.assertEqual({r[2] for r in receipts}, {'starter-sample-synthetic'})
        keys = {k[0] for k in self.store.db.execute(
            'SELECT idempotency_key FROM import_receipts WHERE portfolio_id=?', (pid,))}
        self.assertEqual(keys, {'starter-sample-v1:' + e['symbol']
                                for e in starter_data.load_starter_data()})
        self.assertEqual(self.count('holding_events'), 20)
        self.assertEqual(self.count('baseline_coverage'), 20)
    def test_sample_is_idempotent_and_does_not_resurrect_removed_holding(self):
        self.seed()
        first = self.sample()
        second = self.sample()
        self.assertEqual((second['id'], second['created']), (first['id'], False))
        self.assertEqual(self.count('portfolios'), 1)
        self.assertEqual(self.count('import_receipts'), 20)
        amd = self.store.db.execute("SELECT id FROM securities WHERE symbol='AMD'").fetchone()[0]
        self.app.remove_holding(self.ctx['alice'], first['id'], amd, source='t',
                                idempotency_key='rm-amd-0001', market_as_of=NOW)
        self.sample()
        self.assertEqual(len(self.app.holdings(self.ctx['alice'], first['id'])), 19)
    def test_users_own_same_named_portfolio_is_not_adopted(self):
        self.seed()
        own = self.app.create_portfolio(self.ctx['alice'], NAME)
        result = self.sample()
        self.assertTrue(result['created'])
        self.assertNotEqual(result['id'], own)
        self.assertEqual(self.app.holdings(self.ctx['alice'], own), [])
    def test_samples_are_per_user_and_isolated(self):
        self.seed()
        a, b = self.sample('alice'), self.sample('bob')
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual([p['id'] for p in self.app.portfolios(self.ctx['alice'])], [a['id']])
        with self.assertRaises(PermissionError):
            self.app.holdings(self.ctx['alice'], b['id'])
    def test_valuation_has_every_line_missing_and_no_total(self):
        self.seed()
        pid = self.sample()['id']
        report = self.app.value_portfolio(self.ctx['alice'], pid, as_of=NOW, source='operator-entered')
        self.assertEqual(report['status'], 'incomplete')
        self.assertIsNone(report['total'])
        self.assertEqual(len(report['lines']), 20)
        self.assertEqual({x['status'] for x in report['lines']}, {'missing'})
        self.assertEqual(self.count('market_observations'), 0)
    def test_paper_activity_is_untouched(self):
        self.seed()
        self.sample()
        for table in ('paper_accounts', 'paper_ledger'):
            self.assertEqual(self.count(table), 0)
if __name__ == '__main__':
    unittest.main()
