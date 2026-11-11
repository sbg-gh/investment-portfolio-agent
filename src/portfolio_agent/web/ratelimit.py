"""Token-bucket rate limiter and failed-login throttle (injected clocks, bounded tables)."""
import math
import threading
import time


class RateLimiter:
    def __init__(self, clock=time.monotonic, capacity=60, refill_per_second=30, max_keys=10000):
        self._clock = clock
        self._capacity = capacity
        self._rate = refill_per_second
        self._max = max_keys
        self._buckets = {}
        self._lock = threading.Lock()

    def _refill(self, key, now):
        tokens, last = self._buckets.get(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - last) * self._rate)
        if key not in self._buckets and len(self._buckets) >= self._max:
            self._buckets.pop(next(iter(self._buckets)))
        self._buckets[key] = (tokens, now)
        return tokens

    def allow(self, key):
        with self._lock:
            now = self._clock()
            tokens = self._refill(key, now)
            if tokens < 1:
                return False
            self._buckets[key] = (tokens - 1, now)
            return True

    def retry_after(self, key):
        with self._lock:
            tokens = self._refill(key, self._clock())
            return max(1, math.ceil((1 - tokens) / self._rate))


class LoginThrottle:
    def __init__(self, clock=time.monotonic, per_peer=5, global_limit=50, window=300, max_peers=1024):
        self._clock = clock
        self._per_peer = per_peer
        self._global_limit = global_limit
        self._window = window
        self._max_peers = max_peers
        self._peers = {}
        self._global = []
        self._lock = threading.Lock()

    def _wait(self, times, limit, now):
        times[:] = [t for t in times if now - t < self._window]
        if len(times) < limit:
            return 0
        return max(1, math.ceil(times[len(times) - limit] + self._window - now))

    def check(self, peer):
        with self._lock:
            now = self._clock()
            return max(self._wait(self._peers.get(peer, []), self._per_peer, now),
                       self._wait(self._global, self._global_limit, now))

    def failure(self, peer):
        with self._lock:
            now = self._clock()
            if peer not in self._peers and len(self._peers) >= self._max_peers:
                self._peers.pop(next(iter(self._peers)))
            times = self._peers.setdefault(peer, [])
            times.append(now)
            del times[:-self._per_peer]
            self._global.append(now)
            del self._global[:-self._global_limit]

    def success(self, peer):
        with self._lock:
            self._peers.pop(peer, None)
