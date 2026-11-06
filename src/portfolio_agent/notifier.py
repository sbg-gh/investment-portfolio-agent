"""Deterministic local-sink delivery for notifications.

LocalSinkNotifier is a DETERMINISTIC in-memory sink, NOT a real notification channel or
network adapter. It records deliveries to a list and is idempotent on the per-user dedup
key so an at-least-once retry never appends a duplicate. No live channel, no network.
"""
from .ports import Notifier


class LocalSinkNotifier(Notifier):
    """In-memory, idempotent delivery sink for tests and the local demo.

    This is a deterministic sink, NOT a real channel or network adapter. notify() appends
    (user_id, message, deduplication_key) to self.deliveries only when the key is unseen for
    that user, so a retried delivery (at-least-once) records no duplicate.
    """

    def __init__(self):
        self.deliveries = []
        self._seen = set()

    def notify(self, user_id, message, *, deduplication_key):
        marker = (user_id, deduplication_key)
        if marker in self._seen:
            return
        self._seen.add(marker)
        self.deliveries.append((user_id, message, deduplication_key))
