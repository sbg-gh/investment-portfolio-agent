"""Effective-dated alias resolution contract, written before implementation.

Aliases map a symbol to an ALREADY-registered security as of a date. Resolution
never calls the validator and never auto-registers. Fixtures are obviously synthetic.
"""
from datetime import datetime, timezone
import unittest
from portfolio_agent.core import Store, ValidatedSecurity

FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
MID = datetime(2026, 6, 1, tzinfo=timezone.utc)
TO = datetime(2026, 12, 1, tzinfo=timezone.utc)
BEFORE = datetime(2025, 1, 1, tzinfo=timezone.utc)
AFTER = datetime(2027, 1, 1, tzinfo=timezone.utc)


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for n in range(3):
            with self.s.db:
                self.s.register(ValidatedSecurity(f'syn{n}', f'SYN{n}', 'stock', 'US', 'synthetic'))

    def test_open_ended_window_resolves_from_effective_from_onward(self):
        self.s.register_alias('ALIAS', 'syn0', 'synthetic', FROM)
        self.assertEqual(self.s.resolve_alias('ALIAS', MID), 'syn0')
        self.assertEqual(self.s.resolve_alias('ALIAS', FROM), 'syn0')
        self.assertIsNone(self.s.resolve_alias('ALIAS', BEFORE))

    def test_closed_window_is_half_open(self):
        self.s.register_alias('ALIAS', 'syn0', 'synthetic', FROM, TO)
        self.assertEqual(self.s.resolve_alias('ALIAS', FROM), 'syn0')
        self.assertEqual(self.s.resolve_alias('ALIAS', MID), 'syn0')
        # effective_to is exclusive
        self.assertIsNone(self.s.resolve_alias('ALIAS', TO))
        self.assertIsNone(self.s.resolve_alias('ALIAS', AFTER))

    def test_newest_effective_from_wins(self):
        self.s.register_alias('ALIAS', 'syn0', 'synthetic', FROM)
        self.s.register_alias('ALIAS', 'syn1', 'synthetic', MID)
        self.assertEqual(self.s.resolve_alias('ALIAS', MID), 'syn1')
        self.assertEqual(self.s.resolve_alias('ALIAS', FROM), 'syn0')

    def test_unknown_symbol_returns_none(self):
        self.assertIsNone(self.s.resolve_alias('NOPE', MID))

    def test_register_alias_rejects_unknown_target(self):
        with self.assertRaises(ValueError):
            self.s.register_alias('ALIAS', 'does-not-exist', 'synthetic', FROM)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM security_aliases').fetchone()[0], 0)

    def test_register_alias_rejects_inverted_window(self):
        with self.assertRaises(ValueError):
            self.s.register_alias('ALIAS', 'syn0', 'synthetic', TO, FROM)
        with self.assertRaises(ValueError):
            self.s.register_alias('ALIAS', 'syn0', 'synthetic', FROM, FROM)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM security_aliases').fetchone()[0], 0)

    def test_resolution_never_auto_registers(self):
        before = self.s.db.execute('SELECT COUNT(*) FROM securities').fetchone()[0]
        self.assertIsNone(self.s.resolve_alias('UNKNOWN', MID))
        after = self.s.db.execute('SELECT COUNT(*) FROM securities').fetchone()[0]
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
