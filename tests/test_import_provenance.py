"""Store-level import provenance, audited removal and idempotency contract.

Written before implementation. Fixtures are obviously synthetic; quantities are Decimal
strings and timestamps are UTC ISO-8601.
"""
from datetime import datetime, timezone
import unittest
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity

AS_OF = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
ALIAS_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ImportProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob'):
            self.s.create_user(user)
        self.p = self.s.create_portfolio('alice', 'Private')
        self.v = FixtureValidator({'DEMO': ValidatedSecurity('s0', 'DEMO', 'stock', 'US', 'synthetic')})

    def _import(self, symbol='DEMO', quantity='1.5', key='k1', as_of=AS_OF, source='fixture'):
        return self.s.import_holding_with_provenance(
            'alice', self.p, symbol, quantity, self.v,
            source=source, market_as_of=as_of, idempotency_key=key)

    def test_import_records_receipt_and_event_with_provenance(self):
        sid = self._import()
        self.assertEqual(sid, 's0')
        self.assertEqual(self.s.holdings('alice', self.p), [('s0', '1.5')])
        receipts = self.s.db.execute(
            'SELECT security_id,source,market_as_of,quantity,action FROM import_receipts WHERE portfolio_id=?',
            (self.p,)).fetchall()
        self.assertEqual(receipts, [('s0', 'fixture', AS_OF.isoformat(), '1.5', 'upsert')])
        events = self.s.db.execute(
            'SELECT event,quantity FROM holding_events WHERE portfolio_id=?', (self.p,)).fetchall()
        self.assertEqual(events, [('import', '1.5')])

    def test_idempotent_retry_does_not_double_apply(self):
        self._import(quantity='1.5', key='k1')
        # Repeat with the same key but a different quantity: must not re-apply.
        self._import(quantity='9', key='k1')
        self.assertEqual(self.s.holdings('alice', self.p), [('s0', '1.5')])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 1)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM holding_events').fetchone()[0], 1)

    def test_new_key_applies_again(self):
        self._import(quantity='1.5', key='k1')
        self._import(quantity='4', key='k2')
        self.assertEqual(self.s.holdings('alice', self.p), [('s0', '4')])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 2)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM holding_events').fetchone()[0], 2)

    def test_alias_resolved_import_binds_without_validator(self):
        # Register the target security, then an alias for a symbol the validator does NOT know.
        with self.s.db:
            self.s.register(ValidatedSecurity('aliased', 'REAL', 'stock', 'US', 'synthetic'))
        self.s.register_alias('GHOST', 'aliased', 'synthetic', ALIAS_FROM)
        sid = self._import(symbol='GHOST', quantity='2', key='alias-key')
        self.assertEqual(sid, 'aliased')
        self.assertEqual(self.s.holdings('alice', self.p), [('aliased', '2')])

    def test_alias_miss_falls_through_to_validator_autoregistration(self):
        sid = self._import(symbol='DEMO', quantity='3', key='k1')
        self.assertEqual(sid, 's0')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM securities').fetchone()[0], 1)

    def test_explicit_removal_deletes_holding_and_audits(self):
        self._import(quantity='1.5', key='k1')
        self.s.remove_holding('alice', self.p, 's0', source='fixture',
                              idempotency_key='rm1', market_as_of=AS_OF)
        self.assertEqual(self.s.holdings('alice', self.p), [])
        receipt = self.s.db.execute(
            'SELECT action,quantity FROM import_receipts WHERE idempotency_key=?', ('rm1',)).fetchone()
        self.assertEqual(receipt, ('remove', None))
        event = self.s.db.execute(
            "SELECT event FROM holding_events WHERE event='remove'").fetchone()
        self.assertEqual(event, ('remove',))

    def test_removing_missing_holding_raises_without_side_effects(self):
        with self.assertRaises(ValueError):
            self.s.remove_holding('alice', self.p, 's0', source='fixture',
                                  idempotency_key='rm1', market_as_of=AS_OF)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM holding_events').fetchone()[0], 0)

    def test_removal_is_idempotent_on_key(self):
        self._import(quantity='1.5', key='k1')
        self.s.remove_holding('alice', self.p, 's0', source='fixture',
                              idempotency_key='rm1', market_as_of=AS_OF)
        # Retried removal with the same key: no second receipt/event, no error.
        self.s.remove_holding('alice', self.p, 's0', source='fixture',
                              idempotency_key='rm1', market_as_of=AS_OF)
        self.assertEqual(
            self.s.db.execute("SELECT COUNT(*) FROM import_receipts WHERE action='remove'").fetchone()[0], 1)

    def test_failed_import_leaves_no_side_effects(self):
        with self.assertRaises(ValueError):
            self._import(symbol='UNKNOWN', quantity='1', key='k1')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM securities').fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM holding_events').fetchone()[0], 0)
        self.assertEqual(self.s.holdings('alice', self.p), [])

    def test_cross_user_import_and_removal_denied(self):
        with self.assertRaises(PermissionError):
            self.s.import_holding_with_provenance('bob', self.p, 'DEMO', '1', self.v,
                                                  source='fixture', market_as_of=AS_OF, idempotency_key='k1')
        with self.assertRaises(PermissionError):
            self.s.remove_holding('bob', self.p, 's0', source='fixture',
                                  idempotency_key='rm1', market_as_of=AS_OF)


if __name__ == '__main__':
    unittest.main()
