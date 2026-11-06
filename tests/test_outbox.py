"""Store-level notification outbox + per-user preferences contract.

Written before implementation. Rows stay payload-free of private portfolio context:
event is a fixed label, never a symbol/quantity/holding. Timestamps are UTC ISO-8601;
materiality/threshold are Decimal. The outbox persists a row BEFORE delivery
(at-least-once) and dedups on (user_id, dedup_key). Delivery goes to a deterministic
in-memory LocalSinkNotifier; a retry adds no duplicate.
"""
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from portfolio_agent.core import Store
from portfolio_agent.notifier import LocalSinkNotifier

NOON = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)


class OutboxPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob'):
            self.s.create_user(user)

    # --- preferences ---------------------------------------------------------
    def test_set_and_read_preferences(self):
        self.s.set_notification_preferences(
            'alice', quiet_start=22, quiet_end=7,
            materiality_threshold=Decimal('0.05'), cooldown_seconds=3600)
        prefs = self.s.notification_preferences('alice')
        self.assertEqual(prefs['quiet_start'], 22)
        self.assertEqual(prefs['quiet_end'], 7)
        self.assertEqual(Decimal(prefs['materiality_threshold']), Decimal('0.05'))
        self.assertEqual(prefs['cooldown_seconds'], 3600)
        self.assertEqual(prefs['channel'], 'local-sink')

    def test_preferences_defaults_when_unset(self):
        prefs = self.s.notification_preferences('bob')
        self.assertIsNone(prefs['quiet_start'])
        self.assertIsNone(prefs['quiet_end'])
        self.assertEqual(Decimal(prefs['materiality_threshold']), Decimal('0'))
        self.assertEqual(prefs['cooldown_seconds'], 0)
        self.assertEqual(prefs['channel'], 'local-sink')

    def test_invalid_quiet_hours_raise_without_write(self):
        for qs, qe in ((24, 7), (-1, 7), (22, 24), (5, None)):
            with self.assertRaises(ValueError):
                self.s.set_notification_preferences(
                    'alice', quiet_start=qs, quiet_end=qe,
                    materiality_threshold=Decimal('0'), cooldown_seconds=0)
        self.assertEqual(
            self.s.db.execute('SELECT COUNT(*) FROM notification_preferences').fetchone()[0], 0)

    def test_invalid_materiality_or_cooldown_raise_without_write(self):
        for bad in (Decimal('-1'), Decimal('nan'), Decimal('inf')):
            with self.assertRaises(ValueError):
                self.s.set_notification_preferences(
                    'alice', quiet_start=None, quiet_end=None,
                    materiality_threshold=bad, cooldown_seconds=0)
        with self.assertRaises(ValueError):
            self.s.set_notification_preferences(
                'alice', quiet_start=None, quiet_end=None,
                materiality_threshold=Decimal('0'), cooldown_seconds=-1)
        self.assertEqual(
            self.s.db.execute('SELECT COUNT(*) FROM notification_preferences').fetchone()[0], 0)

    def test_unknown_user_preferences_denied(self):
        with self.assertRaises(ValueError):
            self.s.set_notification_preferences(
                'nobody', quiet_start=None, quiet_end=None,
                materiality_threshold=Decimal('0'), cooldown_seconds=0)

    # --- enqueue persistence + dedup ----------------------------------------
    def test_enqueue_persists_pending_before_delivery(self):
        job_id = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        row = self.s.db.execute(
            'SELECT user_id,event,dedup_key,channel,delivery_status FROM notification_outbox WHERE id=?',
            (job_id,)).fetchone()
        self.assertEqual(row, ('alice', 'price-alert', 'k1', 'local-sink', 'pending'))

    def test_enqueue_dedup_returns_prior_id_no_second_row(self):
        first = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        second = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        self.assertEqual(first, second)
        self.assertEqual(
            self.s.db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0], 1)

    def test_enqueue_unknown_user_raises(self):
        with self.assertRaises(ValueError):
            self.s.enqueue_notification(
                'nobody', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)

    # --- suppression ---------------------------------------------------------
    def test_quiet_hours_suppress(self):
        # quiet window [10,14); NOON (hour 12) is inside.
        self.s.set_notification_preferences(
            'alice', quiet_start=10, quiet_end=14,
            materiality_threshold=Decimal('0'), cooldown_seconds=0)
        job_id = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        status = self.s.db.execute(
            'SELECT delivery_status FROM notification_outbox WHERE id=?', (job_id,)).fetchone()[0]
        self.assertEqual(status, 'suppressed')

    def test_materiality_below_threshold_suppresses(self):
        self.s.set_notification_preferences(
            'alice', quiet_start=None, quiet_end=None,
            materiality_threshold=Decimal('0.10'), cooldown_seconds=0)
        job_id = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON,
            materiality=Decimal('0.05'))
        status = self.s.db.execute(
            'SELECT delivery_status FROM notification_outbox WHERE id=?', (job_id,)).fetchone()[0]
        self.assertEqual(status, 'suppressed')

    def test_materiality_at_or_above_threshold_pending(self):
        self.s.set_notification_preferences(
            'alice', quiet_start=None, quiet_end=None,
            materiality_threshold=Decimal('0.10'), cooldown_seconds=0)
        job_id = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON,
            materiality=Decimal('0.10'))
        status = self.s.db.execute(
            'SELECT delivery_status FROM notification_outbox WHERE id=?', (job_id,)).fetchone()[0]
        self.assertEqual(status, 'pending')

    def test_cooldown_suppresses_second_within_window(self):
        self.s.set_notification_preferences(
            'alice', quiet_start=None, quiet_end=None,
            materiality_threshold=Decimal('0'), cooldown_seconds=3600)
        first = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        self.s.deliver_pending(LocalSinkNotifier(), NOON)  # mark first as sent
        later = datetime(2026, 11, 6, 12, 30, tzinfo=timezone.utc)  # 30 min < 1h cooldown
        second = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k2', channel='local-sink', now=later)
        status = self.s.db.execute(
            'SELECT delivery_status FROM notification_outbox WHERE id=?', (second,)).fetchone()[0]
        self.assertEqual(status, 'suppressed')
        self.assertTrue(first)

    def test_cooldown_allows_second_after_window(self):
        self.s.set_notification_preferences(
            'alice', quiet_start=None, quiet_end=None,
            materiality_threshold=Decimal('0'), cooldown_seconds=3600)
        self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        self.s.deliver_pending(LocalSinkNotifier(), NOON)
        later = datetime(2026, 11, 6, 14, tzinfo=timezone.utc)  # 2h > 1h cooldown
        second = self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k2', channel='local-sink', now=later)
        status = self.s.db.execute(
            'SELECT delivery_status FROM notification_outbox WHERE id=?', (second,)).fetchone()[0]
        self.assertEqual(status, 'pending')

    # --- delivery (at-least-once, idempotent) -------------------------------
    def test_deliver_pending_sends_and_is_idempotent(self):
        self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        notifier = LocalSinkNotifier()
        self.s.deliver_pending(notifier, NOON)
        self.assertEqual(len(notifier.deliveries), 1)
        self.assertEqual(notifier.deliveries[0], ('alice', 'price-alert', 'k1'))
        status = self.s.db.execute(
            "SELECT delivery_status FROM notification_outbox WHERE dedup_key='k1'").fetchone()[0]
        self.assertEqual(status, 'sent')
        # Retry must not append a duplicate delivery.
        self.s.deliver_pending(notifier, NOON)
        self.assertEqual(len(notifier.deliveries), 1)

    def test_deliver_pending_skips_suppressed(self):
        self.s.set_notification_preferences(
            'alice', quiet_start=10, quiet_end=14,
            materiality_threshold=Decimal('0'), cooldown_seconds=0)
        self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        notifier = LocalSinkNotifier()
        self.s.deliver_pending(notifier, NOON)
        self.assertEqual(notifier.deliveries, [])


if __name__ == '__main__':
    unittest.main()
