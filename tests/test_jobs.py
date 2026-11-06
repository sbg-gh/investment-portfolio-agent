"""Durable job store: leases, idempotent enqueue, crash/retry, bounded retries, dead-letter.

Written before implementation. Rows stay payload-free (type/keys/status only). Timestamps are
UTC ISO-8601; the runner is deterministic and test-invoked - no background thread, no network.
"""
from datetime import datetime, timedelta, timezone
import sqlite3
import unittest

from portfolio_agent.core import Store
from portfolio_agent.jobs import (
    LEASE_SECONDS, MAX_ATTEMPTS, MAX_CLAIM_PER_TICK, JobRunner, JobStore)

T0 = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


def _at(seconds):
    return T0 + timedelta(seconds=seconds)


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob'):
            self.s.create_user(user)
        self.jobs = JobStore(self.s)

    def _enqueue(self, *, job_type='monitor', key='k1', due_at=T0, corr='c1',
                 owner_ref='alice', public_scope=None):
        return self.jobs.enqueue(job_type, idempotency_key=key, due_at=due_at,
                                 correlation_id=corr, owner_ref=owner_ref, public_scope=public_scope)

    def test_enqueue_returns_id_and_is_idempotent(self):
        first = self._enqueue(key='k1')
        second = self._enqueue(key='k1')
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)

    def test_owner_xor_scope_rejects_both_or_neither(self):
        with self.assertRaises(ValueError):
            self._enqueue(key='both', owner_ref='alice', public_scope='global')
        with self.assertRaises(ValueError):
            self._enqueue(key='neither', owner_ref=None, public_scope=None)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)

    def test_public_scope_job_enqueues(self):
        job_id = self._enqueue(key='pub', owner_ref=None, public_scope='global')
        row = self.s.db.execute('SELECT public_scope,owner_ref FROM jobs WHERE id=?', (job_id,)).fetchone()
        self.assertEqual(row, ('global', None))

    def test_empty_key_or_correlation_rejected(self):
        with self.assertRaises(ValueError):
            self._enqueue(key=' ')
        with self.assertRaises(ValueError):
            self._enqueue(key='ok', corr=' ')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)

    def test_claim_due_claims_only_due_and_limits(self):
        self._enqueue(key='due', due_at=T0)
        self._enqueue(key='future', due_at=_at(3600))
        claimed = self.jobs.claim_due(T0, limit=MAX_CLAIM_PER_TICK)
        self.assertEqual(len(claimed), 1)
        job_id = claimed[0][0]
        row = self.s.db.execute(
            'SELECT status,attempts,lease_expires_at FROM jobs WHERE id=?', (job_id,)).fetchone()
        self.assertEqual(row[0], 'leased')
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], _at(LEASE_SECONDS).isoformat())

    def test_claim_due_respects_limit(self):
        for i in range(3):
            self._enqueue(key=f'k{i}', due_at=T0)
        claimed = self.jobs.claim_due(T0, limit=2)
        self.assertEqual(len(claimed), 2)

    def test_no_double_claim_within_lease_window(self):
        self._enqueue(key='only', due_at=T0)
        first = self.jobs.claim_due(T0)
        self.assertEqual(len(first), 1)
        second = self.jobs.claim_due(T0)
        self.assertEqual(second, [])

    def test_expired_lease_is_reclaimable(self):
        self._enqueue(key='only', due_at=T0)
        self.jobs.claim_due(T0)
        again = self.jobs.claim_due(_at(LEASE_SECONDS))
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0][0], self.s.db.execute('SELECT id FROM jobs').fetchone()[0])

    def test_crash_retry_applies_effect_exactly_once(self):
        # An idempotent handler keyed by correlation_id: the effect is applied at most once
        # even though the first run 'crashes' after the effect but before complete().
        effects = {}

        class CrashAfterEffect:
            def __init__(self):
                self.crashed = False

            def __call__(self, job_row, store, now):
                corr = job_row['correlation_id']
                if corr not in effects:  # idempotent effect
                    effects[corr] = effects.get(corr, 0) + 1
                if not self.crashed:
                    self.crashed = True
                    raise RuntimeError('simulated crash after effect, before complete')

        handler = CrashAfterEffect()
        runner = JobRunner(self.s, {'monitor': handler})
        self._enqueue(key='crash', due_at=T0, corr='corr-x')
        runner.tick(T0)  # effect applied, handler raises -> fail() re-arms
        self.assertEqual(effects['corr-x'], 1)
        status = self.s.db.execute('SELECT status FROM jobs').fetchone()[0]
        self.assertIn(status, ('pending', 'leased'))
        runner.tick(_at(LEASE_SECONDS))  # resume, no duplicate effect
        self.assertEqual(effects['corr-x'], 1)
        self.assertEqual(self.s.db.execute('SELECT status FROM jobs').fetchone()[0], 'done')

    def test_bounded_retries_route_to_dead(self):
        def always_fail(job_row, store, now):
            raise RuntimeError('boom')

        runner = JobRunner(self.s, {'monitor': always_fail})
        self._enqueue(key='bad', due_at=T0, corr='corr-dead')
        now = T0
        for _ in range(MAX_ATTEMPTS):
            runner.tick(now)
            now = _at((now - T0).total_seconds() + LEASE_SECONDS)
        self.assertEqual(self.s.db.execute('SELECT status FROM jobs').fetchone()[0], 'dead')
        # A dead job is never claimed again.
        self.assertEqual(self.jobs.claim_due(now), [])
        self.assertEqual(self.s.db.execute('SELECT attempts FROM jobs').fetchone()[0], MAX_ATTEMPTS)

    def test_review_queue_lists_dead_jobs(self):
        def always_fail(job_row, store, now):
            raise RuntimeError('boom')

        runner = JobRunner(self.s, {'monitor': always_fail})
        self._enqueue(key='bad', due_at=T0)
        now = T0
        for _ in range(MAX_ATTEMPTS):
            runner.tick(now)
            now = _at((now - T0).total_seconds() + LEASE_SECONDS)
        review = self.jobs.review_queue()
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]['status'], 'dead')

    def test_complete_marks_done(self):
        def ok(job_row, store, now):
            return None

        runner = JobRunner(self.s, {'monitor': ok})
        self._enqueue(key='good', due_at=T0)
        runner.tick(T0)
        self.assertEqual(self.s.db.execute('SELECT status FROM jobs').fetchone()[0], 'done')


if __name__ == '__main__':
    unittest.main()
