"""Store-level enhanced-monitoring admission machine. Written before implementation.

States: first 75 distinct securities -> admitted (active monitoring set); next ones ->
queued (FIFO, not active) up to the configured queue bound; beyond that -> rejected with
nothing written. No auto-eviction; re-proposal is idempotent. Admission never blocks a
valid holding import.
"""
from datetime import datetime, timedelta, timezone
import unittest
from portfolio_agent.core import (
    ADMISSION_QUEUE_CAPACITY, FixtureValidator, MONITORING_CAPACITY, Store, ValidatedSecurity)

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        self.s.create_user('alice')
        self.p = self.s.create_portfolio('alice', 'Private')
        # Register well over the active cap plus queue bound of distinct securities.
        total = MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY + 2
        for n in range(total):
            with self.s.db:
                self.s.register(self.security(n))

    def security(self, n):
        return ValidatedSecurity(f'm{n}', f'M{n}', 'stock', 'US', 'fixture')

    def _propose(self, n, at=NOW):
        return self.s.propose_enhancement(f'm{n}', reason='fixture', operator='admin', now=at)

    def test_first_capacity_distinct_are_admitted_and_active(self):
        for n in range(MONITORING_CAPACITY):
            self.assertEqual(self._propose(n), 'admitted')
        active = self.s.db.execute("SELECT COUNT(*) FROM monitoring").fetchone()[0]
        self.assertEqual(active, MONITORING_CAPACITY)

    def test_next_distinct_is_queued_not_active(self):
        for n in range(MONITORING_CAPACITY):
            self._propose(n)
        self.assertEqual(self._propose(MONITORING_CAPACITY), 'queued')
        # The queued security must NOT be in the active monitoring set.
        active = {row[0] for row in self.s.db.execute("SELECT security_id FROM monitoring").fetchall()}
        self.assertNotIn(f'm{MONITORING_CAPACITY}', active)
        self.assertEqual(len(active), MONITORING_CAPACITY)

    def test_queue_fills_then_rejects(self):
        for n in range(MONITORING_CAPACITY):
            self._propose(n)
        for n in range(MONITORING_CAPACITY, MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY):
            self.assertEqual(self._propose(n), 'queued')
        # Queue is full; the next distinct security is rejected and nothing is written.
        rejected = MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY
        self.assertEqual(self._propose(rejected), 'rejected')
        self.assertIsNone(self.s.db.execute(
            "SELECT 1 FROM monitoring_admissions WHERE security_id=?", (f'm{rejected}',)).fetchone())

    def test_reproposal_is_idempotent(self):
        self.assertEqual(self._propose(0), 'admitted')
        self.assertEqual(self._propose(0), 'admitted')
        for n in range(1, MONITORING_CAPACITY):
            self._propose(n)
        self.assertEqual(self._propose(MONITORING_CAPACITY), 'queued')
        self.assertEqual(self._propose(MONITORING_CAPACITY), 'queued')
        # No duplicate admission rows.
        self.assertEqual(self.s.db.execute(
            "SELECT COUNT(*) FROM monitoring_admissions WHERE security_id=?", ('m0',)).fetchone()[0], 1)
        self.assertEqual(self.s.db.execute(
            "SELECT COUNT(*) FROM monitoring_admissions WHERE security_id=?",
            (f'm{MONITORING_CAPACITY}',)).fetchone()[0], 1)

    def test_valid_holding_imports_while_tier_and_queue_full(self):
        for n in range(MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY):
            self._propose(n)
        # A valid holding of yet another registered security still imports (independence).
        v = FixtureValidator({'EXTRA': self.security(MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY + 1)})
        sid = self.s.import_holding('alice', self.p, 'EXTRA', '1', v)
        self.assertEqual(self.s.holdings('alice', self.p), [(sid, '1')])

    def test_queue_order_is_deterministic_fifo(self):
        for n in range(MONITORING_CAPACITY):
            self._propose(n)
        # Queue three securities at distinct proposed_at times, out of security_id order.
        self._propose(MONITORING_CAPACITY + 2, at=NOW)
        self._propose(MONITORING_CAPACITY, at=NOW + timedelta(minutes=1))
        self._propose(MONITORING_CAPACITY + 1, at=NOW + timedelta(minutes=2))
        queued = [row[0] for row in self.s.db.execute(
            "SELECT security_id FROM monitoring_admissions WHERE state='queued' ORDER BY position").fetchall()]
        self.assertEqual(queued, [f'm{MONITORING_CAPACITY + 2}', f'm{MONITORING_CAPACITY}',
                                  f'm{MONITORING_CAPACITY + 1}'])

    def test_monitoring_admissions_lists_active_then_queued(self):
        for n in range(MONITORING_CAPACITY):
            self._propose(n)
        self._propose(MONITORING_CAPACITY)
        rows = self.s.monitoring_admissions()
        self.assertEqual(len(rows), MONITORING_CAPACITY + 1)
        states = [r[1] for r in rows]
        self.assertEqual(states[-1], 'queued')
        self.assertTrue(all(state == 'admitted' for state in states[:-1]))


if __name__ == '__main__':
    unittest.main()
