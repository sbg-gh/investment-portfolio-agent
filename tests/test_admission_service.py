"""Operator-gated enhanced-monitoring admission at the authorized boundary.

Written before implementation. Membership administration is operator-only; a non-operator
context is denied by the role gate. Admit/queue/reject statuses surface through
OperatorService.propose_enhancement and run through the atomic metadata-only trace path.
"""
import unittest
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import (
    ADMISSION_QUEUE_CAPACITY, MONITORING_CAPACITY, Store, ValidatedSecurity)
from portfolio_agent.services import OperatorService, PortfolioService


class AdmissionServiceTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'admin'):
            self.s.create_user(user)
        self.auth = Authenticator(self.s)
        self.ctx = {u: self.auth.authenticate(
            self.auth.provision(u, role='operator' if u == 'admin' else 'user'))
            for u in ('alice', 'admin')}
        self.app = PortfolioService(self.s, self.auth, None)
        self.ops = OperatorService(self.app)
        total = MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY + 2
        for n in range(total):
            with self.s.db:
                self.s.register(self.security(n))

    def security(self, n):
        return ValidatedSecurity(f'm{n}', f'M{n}', 'stock', 'US', 'fixture')

    def test_operator_can_propose_and_list(self):
        self.assertEqual(self.ops.propose_enhancement(self.ctx['admin'], 'm0', reason='fixture'), 'admitted')
        rows = self.ops.list_monitoring(self.ctx['admin'])
        self.assertEqual([r[0] for r in rows], ['m0'])

    def test_non_operator_is_denied(self):
        with self.assertRaises(PermissionError):
            self.ops.propose_enhancement(self.ctx['alice'], 'm0', reason='fixture')
        with self.assertRaises(PermissionError):
            self.ops.list_monitoring(self.ctx['alice'])
        # Denied proposal wrote nothing.
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM monitoring_admissions").fetchone()[0], 0)

    def test_admit_queue_reject_statuses_surface(self):
        for n in range(MONITORING_CAPACITY):
            self.assertEqual(self.ops.propose_enhancement(self.ctx['admin'], f'm{n}', reason='fixture'), 'admitted')
        self.assertEqual(
            self.ops.propose_enhancement(self.ctx['admin'], f'm{MONITORING_CAPACITY}', reason='fixture'), 'queued')
        for n in range(MONITORING_CAPACITY + 1, MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY):
            self.ops.propose_enhancement(self.ctx['admin'], f'm{n}', reason='fixture')
        rejected = MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY
        self.assertEqual(
            self.ops.propose_enhancement(self.ctx['admin'], f'm{rejected}', reason='fixture'), 'rejected')

    def test_failed_action_records_metadata_only_trace(self):
        # Empty reason fails validation inside the action; a metadata-only trace is still recorded.
        with self.assertRaises(ValueError):
            self.ops.propose_enhancement(self.ctx['admin'], 'm0', reason='   ')
        traces = self.s.db.execute(
            "SELECT operation,outcome FROM execution_traces WHERE user_id='admin' ORDER BY rowid").fetchall()
        outcomes = {op: outcome for op, outcome in traces}
        self.assertEqual(outcomes['propose_enhancement'], 'invalid')
        # The trace carries no private/admission payload.
        self.assertNotIn('m0', str(traces))


if __name__ == '__main__':
    unittest.main()
