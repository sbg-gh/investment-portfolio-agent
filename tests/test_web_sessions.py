"""In-memory sessions: digest-only storage, idle and absolute expiry, rotation, revocation."""
import threading
import unittest

from portfolio_agent.web.sessions import SessionStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(clock=self.clock, idle=1800, absolute=28800)

    def test_create_returns_unguessable_id_and_csrf(self):
        sid, session = self.store.create('cred-1')
        self.assertGreaterEqual(len(sid), 43)
        self.assertNotEqual(sid, session.csrf)
        self.assertEqual(session.credential_id, 'cred-1')
        self.assertNotEqual(self.store.create('cred-1')[0], sid)

    def test_only_digest_is_stored(self):
        sid, _ = self.store.create('cred-1')
        self.assertNotIn(sid, repr(self.store._sessions))
        self.assertNotIn(sid, repr(self.store.__dict__))

    def test_lookup_round_trip_and_unknown_or_malformed_ids(self):
        sid, _ = self.store.create('c')
        self.assertEqual(self.store.lookup(sid).credential_id, 'c')
        for bad in ('', 'x', sid[:-1], sid + 'a', None, 5, b'x', 'a' * 500, '../../etc', sid.upper(),
                    sid + '\n', ' ' + sid):
            self.assertIsNone(self.store.lookup(bad), repr(bad))

    def test_idle_expiry_and_sliding_window(self):
        sid, _ = self.store.create('c')
        self.clock.now += 1799
        self.assertIsNotNone(self.store.lookup(sid))
        self.clock.now += 1799
        self.assertIsNotNone(self.store.lookup(sid))
        self.clock.now += 1801
        self.assertIsNone(self.store.lookup(sid))
        self.clock.now -= 1801
        self.assertIsNone(self.store.lookup(sid), 'expired sessions stay dead')

    def test_absolute_expiry_even_when_active(self):
        sid, _ = self.store.create('c')
        for _ in range(20):
            self.clock.now += 1700
            if self.clock.now - 1000 >= 28800:
                break
            self.assertIsNotNone(self.store.lookup(sid))
        self.clock.now = 1000 + 28800
        self.assertIsNone(self.store.lookup(sid))

    def test_credential_expiry_caps_lifetime(self):
        sid, _ = self.store.create('c', credential_expires_at=1100)
        self.clock.now = 1099
        self.assertIsNotNone(self.store.lookup(sid))
        self.clock.now = 1100
        self.assertIsNone(self.store.lookup(sid))

    def test_rotation_drops_presented_session(self):
        old, _ = self.store.create('c')
        new, _ = self.store.create('c', replace=old)
        self.assertNotEqual(old, new)
        self.assertIsNone(self.store.lookup(old))
        self.assertIsNotNone(self.store.lookup(new))

    def test_invalidate_and_invalidate_credential(self):
        a, _ = self.store.create('c1')
        b, _ = self.store.create('c1')
        d, _ = self.store.create('c2')
        self.store.invalidate(a)
        self.assertIsNone(self.store.lookup(a))
        self.assertEqual(self.store.invalidate_credential('c1'), 1)
        self.assertIsNone(self.store.lookup(b))
        self.assertIsNotNone(self.store.lookup(d))
        self.store.invalidate('nonexistent')
        self.store.invalidate(None)

    def test_csrf_compare_is_exact_and_type_safe(self):
        _, session = self.store.create('c')
        self.assertTrue(self.store.check_csrf(session, session.csrf))
        for bad in (None, '', 'x', session.csrf + 'x', session.csrf[:-1], 5, b'x', session.csrf.upper()):
            self.assertFalse(self.store.check_csrf(session, bad), repr(bad))

    def test_capacity_evicts_least_recently_seen(self):
        store = SessionStore(clock=self.clock, idle=1800, absolute=28800, max_sessions=3)
        ids = []
        for i in range(3):
            self.clock.now += 1
            ids.append(store.create(f'c{i}')[0])
        self.clock.now += 1
        store.lookup(ids[0])
        self.clock.now += 1
        store.create('c3')
        self.assertIsNone(store.lookup(ids[1]))
        self.assertIsNotNone(store.lookup(ids[0]))

    def test_thread_safety_smoke(self):
        errors = []

        def work():
            try:
                for _ in range(200):
                    sid, _ = self.store.create('c')
                    self.store.lookup(sid)
                    self.store.invalidate(sid)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
