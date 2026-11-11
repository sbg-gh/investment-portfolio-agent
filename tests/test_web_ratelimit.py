"""Token-bucket limiter and login throttle with an injected clock."""
import unittest

from portfolio_agent.web.ratelimit import LoginThrottle, RateLimiter


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class RateLimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.limiter = RateLimiter(clock=self.clock, capacity=3, refill_per_second=1)

    def test_burst_then_block_then_refill(self):
        self.assertEqual([self.limiter.allow('a') for _ in range(4)], [True, True, True, False])
        self.clock.now += 1
        self.assertTrue(self.limiter.allow('a'))
        self.assertFalse(self.limiter.allow('a'))

    def test_keys_are_independent(self):
        for _ in range(3):
            self.limiter.allow('a')
        self.assertFalse(self.limiter.allow('a'))
        self.assertTrue(self.limiter.allow('b'))

    def test_retry_after_is_positive_whole_seconds(self):
        for _ in range(3):
            self.limiter.allow('a')
        self.assertEqual(self.limiter.retry_after('a'), 1)

    def test_capacity_never_exceeded_after_long_idle(self):
        self.clock.now += 10**6
        self.assertEqual(sum(self.limiter.allow('a') for _ in range(10)), 3)

    def test_key_table_is_bounded(self):
        limiter = RateLimiter(clock=self.clock, capacity=1, refill_per_second=1, max_keys=10)
        for i in range(1000):
            limiter.allow(f'k{i}')
        self.assertLessEqual(len(limiter._buckets), 10)


class LoginThrottleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.t = LoginThrottle(clock=self.clock, per_peer=3, global_limit=5, window=60)

    def test_blocks_after_per_peer_failures_and_recovers(self):
        for _ in range(3):
            self.assertEqual(self.t.check('p'), 0)
            self.t.failure('p')
        self.assertGreater(self.t.check('p'), 0)
        self.assertEqual(self.t.check('other'), 0)
        self.clock.now += 61
        self.assertEqual(self.t.check('p'), 0)

    def test_success_clears_peer_failures(self):
        self.t.failure('p')
        self.t.failure('p')
        self.t.success('p')
        self.t.failure('p')
        self.assertEqual(self.t.check('p'), 0)

    def test_global_window_blocks_distributed_guessing(self):
        for i in range(5):
            self.t.failure(f'peer-{i}')
        self.assertGreater(self.t.check('brand-new-peer'), 0)
        self.clock.now += 61
        self.assertEqual(self.t.check('brand-new-peer'), 0)

    def test_success_does_not_clear_global_failures(self):
        for i in range(5):
            self.t.failure(f'peer-{i}')
        self.t.success('peer-0')
        self.assertGreater(self.t.check('peer-0'), 0)

    def test_retry_after_counts_down(self):
        for _ in range(3):
            self.t.failure('p')
        first = self.t.check('p')
        self.clock.now += 10
        self.assertEqual(self.t.check('p'), first - 10)

    def test_peer_table_is_bounded(self):
        t = LoginThrottle(clock=self.clock, per_peer=3, global_limit=10**9, window=60, max_peers=10)
        for i in range(1000):
            t.failure(f'p{i}')
        self.assertLessEqual(len(t._peers), 10)


if __name__ == '__main__':
    unittest.main()
