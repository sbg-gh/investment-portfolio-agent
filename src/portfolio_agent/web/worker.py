"""One dedicated thread owns the Store (sqlite connection has thread affinity).

Every service call runs there, serialised. A slow model call inside PortfolioService._run holds
BEGIN IMMEDIATE on this thread, so it blocks every other request until it returns. Known limit;
the pending queue is bounded and overflow becomes a fast 503 instead of unbounded waiting.
"""
from concurrent.futures import Future
import queue
import threading


class WorkerBusy(Exception):
    """Queue full or worker closed."""


class Direct:
    """Inline executor for single-threaded tests: same submit(fn) contract as Worker."""
    def __init__(self, backend):
        self.backend = backend

    def submit(self, fn):
        return fn(self.backend)


class Worker:
    def __init__(self, factory, max_pending=16):
        self._queue = queue.Queue(maxsize=max_pending)
        self._closed = False
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._failure = None
        self._thread = threading.Thread(target=self._loop, args=(factory,), daemon=True,
                                        name='portfolio-agent-worker')
        self._thread.start()
        self._ready.wait()
        if self._failure is not None:
            raise self._failure

    def _loop(self, factory):
        try:
            resource = factory()
        except BaseException as exc:
            self._failure = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                fn, future = item
                try:
                    future.set_result(fn(resource))
                except BaseException as exc:
                    future.set_exception(exc)
        finally:
            close = getattr(resource, 'close', None)
            if callable(close):
                close()

    def submit(self, fn):
        future = Future()
        with self._lock:
            if self._closed:
                raise WorkerBusy()
            try:
                self._queue.put_nowait((fn, future))
            except queue.Full:
                raise WorkerBusy() from None
        return future.result()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)
        self._thread.join(5)
