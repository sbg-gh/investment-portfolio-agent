"""Durable, deterministic job store with leases, bounded retries and a dead-letter status.

A test-invokable JobRunner polls due jobs over Store.transaction() (BEGIN IMMEDIATE) with
NO background thread. Rows stay payload-free: type/keys/status/timestamps only, never private
portfolio context. Timestamps are UTC ISO-8601. An expired-lease job becomes immediately due
again (deterministic due-time only, no backoff); exhausting MAX_ATTEMPTS routes to 'dead'.
"""
from datetime import timedelta
import sqlite3
from uuid import uuid4

from .valuation import utc

LEASE_SECONDS = 300
MAX_ATTEMPTS = 3
MAX_CLAIM_PER_TICK = 8

_CLAIM_COLUMNS = ("id", "type", "owner_ref", "public_scope", "idempotency_key",
                  "due_at", "lease_expires_at", "attempts", "status", "correlation_id")


class JobStore:
    """Trusted internal persistence for durable jobs. Not an auth boundary."""

    def __init__(self, store):
        self.store = store
        self.db = store.db

    def enqueue(self, job_type, *, idempotency_key, due_at, correlation_id,
                owner_ref=None, public_scope=None):
        """Insert one durable job; dedup on (type, idempotency_key) returning the prior id.

        Exactly one of owner_ref/public_scope must be set (XOR). Identity is NEVER derived
        here from a payload; owner_ref is a trusted reference re-authorized at execution time.
        """
        if not job_type or not str(job_type).strip():
            raise ValueError("Job type required")
        if (owner_ref is None) == (public_scope is None):
            raise ValueError("Exactly one of owner_ref or public_scope is required")
        if not idempotency_key or not str(idempotency_key).strip():
            raise ValueError("Idempotency key required")
        if not correlation_id or not str(correlation_id).strip():
            raise ValueError("Correlation id required")
        when = utc(due_at).isoformat()
        existing = self.db.execute(
            "SELECT id FROM jobs WHERE type=? AND idempotency_key=?",
            (job_type, idempotency_key)).fetchone()
        if existing is not None:
            return existing[0]
        job_id = str(uuid4())
        with self.store.transaction():
            self.db.execute(
                "INSERT INTO jobs(id,type,owner_ref,public_scope,idempotency_key,due_at,correlation_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_id, job_type, owner_ref, public_scope, idempotency_key, when, correlation_id))
        return job_id

    def claim_due(self, now, limit=MAX_CLAIM_PER_TICK):
        """Atomically claim due pending jobs and expired-lease leased jobs, up to `limit`.

        Sets status='leased', lease_expires_at = now + LEASE_SECONDS, attempts += 1, and
        returns the claimed rows (as mappings) ordered by due_at, id.
        """
        at = utc(now).isoformat()
        lease = (utc(now) + timedelta(seconds=LEASE_SECONDS)).isoformat()
        claimed = []
        with self.store.transaction():
            rows = self.db.execute(
                "SELECT id FROM jobs WHERE (status='pending' AND due_at<=?) "
                "OR (status='leased' AND lease_expires_at<=?) "
                "ORDER BY due_at, id LIMIT ?",
                (at, at, int(limit))).fetchall()
            for (job_id,) in rows:
                self.db.execute(
                    "UPDATE jobs SET status='leased', lease_expires_at=?, attempts=attempts+1 WHERE id=?",
                    (lease, job_id))
                claimed.append(self._row(job_id))
        return claimed

    def complete(self, job_id):
        with self.store.transaction():
            self.db.execute("UPDATE jobs SET status='done', lease_expires_at=NULL WHERE id=?", (job_id,))

    def fail(self, job_id, now):
        """Re-arm a failed job (due immediately, no backoff) or route to 'dead' once exhausted."""
        with self.store.transaction():
            attempts = self.db.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            status = 'dead' if attempts >= MAX_ATTEMPTS else 'pending'
            self.db.execute(
                "UPDATE jobs SET status=?, lease_expires_at=NULL WHERE id=?", (status, job_id))

    def recover_expired(self, now):
        """Reset leased jobs whose lease has expired back to a claimable pending state."""
        at = utc(now).isoformat()
        with self.store.transaction():
            self.db.execute(
                "UPDATE jobs SET status='pending', lease_expires_at=NULL "
                "WHERE status='leased' AND lease_expires_at<=?", (at,))

    def review_queue(self):
        """Return dead jobs (as mappings) for operator review. Payload-free."""
        rows = self.db.execute(
            "SELECT id FROM jobs WHERE status='dead' ORDER BY due_at, id").fetchall()
        return [self._row(job_id) for (job_id,) in rows]

    def _row(self, job_id):
        # sqlite3.Row supports both positional (row[0]) and name (row['type']) access.
        cursor = self.db.execute(
            f"SELECT {','.join(_CLAIM_COLUMNS)} FROM jobs WHERE id=?", (job_id,))
        cursor.row_factory = sqlite3.Row
        return cursor.fetchone()


def build_default_handlers(store):
    """Return the {job_type: callable} registry for the proactive service.

    Discovered at construction and passed to JobRunner; peers are NOT hardcoded into the
    runner. 'collect' is the public, coalesced, holder-blind step; 'monitor_portfolio' is
    the private step that re-authorizes the current owner at execution time.
    """
    from . import monitoring
    return {
        'collect': monitoring.collect_handler,
        'monitor_portfolio': monitoring.monitor_portfolio_handler,
    }


class JobRunner:
    """Deterministic, test-invoked poller. Handlers are a registry discovered at construction.

    handlers maps {job_type: callable(job_row, store, now)}; peers are NOT hardcoded. One
    handler failure never aborts the whole tick.
    """

    def __init__(self, store, handlers):
        self.store = store
        self.jobs = JobStore(store)
        self.handlers = dict(handlers)

    def tick(self, now, limit=MAX_CLAIM_PER_TICK):
        return self.run_due(now, limit=limit)

    def run_due(self, now, limit=MAX_CLAIM_PER_TICK):
        self.jobs.recover_expired(now)
        claimed = self.jobs.claim_due(now, limit=limit)
        for job in claimed:
            handler = self.handlers.get(job['type'])
            if handler is None:
                self.jobs.fail(job['id'], now)
                continue
            try:
                with self.store.transaction():
                    handler(job, self.store, now)
                self.jobs.complete(job['id'])
            except Exception:
                self.jobs.fail(job['id'], now)
        return claimed
