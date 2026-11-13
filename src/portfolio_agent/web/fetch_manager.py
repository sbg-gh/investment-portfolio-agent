"""Background Yahoo press for the operator route: one at a time, in memory, off the store worker.

The web layer serves every request through ONE worker thread that owns the SQLite store, and a
press takes tens of seconds. So the network phase runs HERE, on its own daemon thread, with no
store and no transaction; the store worker is used only for two short operator-only calls: reading
the targets and ONE ingest. Both re-authenticate the starting credential by id (revocation, expiry
and the operator role are checked again at that moment), so a credential revoked mid-fetch writes
nothing. Status is process-local and lost on restart by design (the UI shows "interrupted").
Only the latest record is kept: a new press replaces the previous one. Records hold fixed codes,
counts, dates and closes, never provider text; nothing here logs.
"""
from datetime import datetime, timezone
import threading
from uuid import uuid4
from .. import price_fetch
from .worker import WorkerBusy
from .wire import WireError
MAX_RESULT_SYMBOLS = 500
class _Interrupted(Exception):
    pass
def _as_operator(backend, credential_id, call):
    context, _ = backend.auth.reauthenticate(credential_id)   # PermissionError if revoked or expired
    return call(context)                                     # the service re-checks the operator role
def _now():
    return datetime.now(timezone.utc).isoformat()
class FetchManager:
    def __init__(self, executor, *, transport=None, clock=None, sleeper=None, rng=None, join_timeout=5.0):
        self._executor, self._join_timeout = executor, join_timeout
        self._transport, self._clock, self._rng = transport, clock, rng
        self._stop = threading.Event()
        self._sleeper = sleeper or self._stop.wait   # interruptible spacing and backoff
        self._lock = threading.Lock()
        self._record = None
        self._thread = None
    # --- request side (called from web threads) ---------------------------------------------
    def start(self, user_id, credential_id, dry_run):
        with self._lock:
            if self._stop.is_set():
                raise WireError('unavailable', 503)
            if self._record is not None and self._record['state'] == 'running':
                raise WireError('busy', 409)
            fetch_id = uuid4().hex
            self._record = {'fetch_id': fetch_id, 'owner': user_id, 'state': 'running', 'dry_run': dry_run,
                            'progress': {'done': 0, 'total': 0}, 'started_at': _now(),
                            'finished_at': None, 'result': None, 'error': None}
            self._thread = threading.Thread(target=self._run, args=(self._record, credential_id, dry_run),
                                            daemon=True, name='portfolio-agent-price-fetch')
            self._thread.start()
            return fetch_id
    def status(self, user_id, fetch_id):
        """The owner's record, or the same fixed `unknown` for an absent, lost or foreign id."""
        with self._lock:
            record = self._record
            if record is None or record['fetch_id'] != fetch_id or record['owner'] != user_id:
                return {'state': 'unknown'}
            return self._public(record)
    def snapshot_for(self, user_id):
        with self._lock:
            record = self._record
            return self._public(record) if record is not None and record['owner'] == user_id else None
    @staticmethod
    def _public(record):
        return {key: (dict(value) if isinstance(value, dict) else value)
                for key, value in record.items() if key != 'owner'}
    def thread_alive(self):
        return self._thread is not None and self._thread.is_alive()
    def close(self, join_timeout=None):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(self._join_timeout if join_timeout is None else join_timeout)
    # --- background thread -------------------------------------------------------------------
    def _progress(self, record):
        def update(done, total):
            with self._lock:
                record['progress'] = {'done': done, 'total': total}
        return update
    def _finish(self, record, state, result=None, error=None):
        with self._lock:
            record.update(state=state, result=result, error=error, finished_at=_now())
    def _run(self, record, credential_id, dry_run):
        def ingest(rows, dry):
            if self._stop.is_set():
                raise _Interrupted()
            return self._executor.submit(lambda b: _as_operator(b, credential_id, lambda ctx: b.ops.import_rows(
                ctx, source=price_fetch.SOURCE, rows=rows, dry_run=dry,
                now=self._clock() if self._clock else None)))   # one clock for fetch and ingest (tests inject it)
        try:
            targets = self._executor.submit(lambda b: _as_operator(
                b, credential_id, lambda ctx: b.ops.price_fetch_targets(ctx)))
            with self._lock:
                record['progress'] = {'done': 0, 'total': len(targets)}
            result = price_fetch.run_press(targets, ingest, dry_run=dry_run, transport=self._transport,
                                           clock=self._clock, sleeper=self._sleeper, rng=self._rng,
                                           should_stop=self._stop.is_set, on_progress=self._progress(record))
            symbols = result['symbols']
            result['omitted'] = max(0, len(symbols) - MAX_RESULT_SYMBOLS)
            result['symbols'] = symbols[:MAX_RESULT_SYMBOLS]
            self._finish(record, 'done', result)
        except _Interrupted:
            self._finish(record, 'failed', error='interrupted')
        except PermissionError:
            self._finish(record, 'failed', error='forbidden')
        except WorkerBusy:
            self._finish(record, 'failed', error='busy')
        except Exception:   # fixed code only: no exception text is kept or logged
            self._finish(record, 'failed', error='internal')
