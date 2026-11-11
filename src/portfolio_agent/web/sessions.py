"""In-memory sessions. The id is a 256-bit secret kept only as a SHA-256 digest."""
from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
import threading
import time

_SID = re.compile(r'[A-Za-z0-9_-]{43}')


@dataclass
class Session:
    credential_id: str
    csrf: str
    created: float
    last_seen: float
    cap: float | None


class SessionStore:
    def __init__(self, clock=time.time, idle=1800, absolute=28800, max_sessions=1000):
        self._clock = clock
        self._idle = idle
        self._absolute = absolute
        self.absolute = absolute
        self._max = max_sessions
        self._sessions = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(sid):
        if not isinstance(sid, str) or not _SID.fullmatch(sid):
            return None
        return hashlib.sha256(sid.encode()).hexdigest()

    def _expired(self, session, now):
        return (now - session.created >= self._absolute or now - session.last_seen > self._idle
                or (session.cap is not None and now >= session.cap))

    def create(self, credential_id, credential_expires_at=None, replace=None):
        now = self._clock()
        sid = secrets.token_urlsafe(32)
        session = Session(credential_id, secrets.token_urlsafe(32), now, now, credential_expires_at)
        with self._lock:
            digest = self._digest(replace)
            if digest:
                self._sessions.pop(digest, None)
            for key in [k for k, s in self._sessions.items() if self._expired(s, now)]:
                del self._sessions[key]
            while len(self._sessions) >= self._max:
                oldest = min(self._sessions, key=lambda k: self._sessions[k].last_seen)
                del self._sessions[oldest]
            self._sessions[self._digest(sid)] = session
        return sid, session

    def lookup(self, sid):
        digest = self._digest(sid)
        if digest is None:
            return None
        now = self._clock()
        with self._lock:
            session = self._sessions.get(digest)
            if session is None:
                return None
            if self._expired(session, now):
                del self._sessions[digest]
                return None
            session.last_seen = now
            return session

    def invalidate(self, sid):
        digest = self._digest(sid)
        if digest:
            with self._lock:
                self._sessions.pop(digest, None)

    def invalidate_credential(self, credential_id):
        with self._lock:
            doomed = [k for k, s in self._sessions.items() if s.credential_id == credential_id]
            for key in doomed:
                del self._sessions[key]
        return len(doomed)

    @staticmethod
    def check_csrf(session, supplied):
        if not isinstance(supplied, str):
            return False
        return hmac.compare_digest(session.csrf.encode(), supplied.encode('utf-8'))
