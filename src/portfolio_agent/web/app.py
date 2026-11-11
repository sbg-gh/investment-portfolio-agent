"""Framework-neutral request core: routing, limits, Host/Origin/CSRF, sessions, error mapping.

SECURITY-SENSITIVE and hand written (no framework). Order of checks:
size/header shape -> Host -> rate limit -> route match (generic 404/405) -> Origin and
Fetch-Metadata on unsafe methods -> session (identity ONLY from the server-side session, then a
fresh credential check) -> CSRF -> operator role -> strict JSON body -> handler -> error mapping
-> security headers -> payload-free log line. Imports no third-party package.
"""
from dataclasses import dataclass, field
import re
import time
from typing import Any, Callable
from uuid import uuid4

from . import logsafe, policy, wire
from .policy import PolicyError
from .worker import WorkerBusy

COOKIE = 'pa_session'
SAFE_METHODS = frozenset({'GET', 'HEAD'})
KNOWN_METHODS = frozenset({'GET', 'POST', 'DELETE'})
MAX_PATH = 512
MAX_QUERY = 2048
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
_STATIC = re.compile(r'[A-Za-z0-9_-]{1,64}')
UNAUTHENTICATED = {'error': 'unauthenticated'}


@dataclass
class Request:
    method: Any
    path: Any
    query: str
    headers: list
    body: bytes
    peer: str
    scheme: str = 'http'
    app: Any = None
    request_id: str = ''
    params: dict = field(default_factory=dict)
    json: dict = field(default_factory=dict)
    session: Any = None
    user_id: str | None = None
    role: str | None = None
    context: Any = None
    sid: str | None = None
    template: str | None = None

    def header(self, name):
        for key, value in self.headers:
            if key == name:
                return value
        return None

    def run(self, fn):
        """Run fn(backend) on the worker thread that owns the Store."""
        return self.app._executor.submit(fn)


@dataclass(frozen=True)
class Response:
    status: int
    headers: list
    body: bytes


@dataclass(frozen=True)
class Route:
    method: str
    template: str
    handler: Callable
    private: bool = False
    unsafe: bool = False
    operator: bool = False
    stream: bool = False  # handler validates and returns produce(); produce() runs later (SSE)

    @property
    def segments(self):
        return self.template.strip('/').split('/')


def match_path(route, path):
    """Return params dict or None. Only unencoded, normalised, bounded segments can match."""
    parts = path[1:].split('/')
    if len(parts) != len(route.segments):
        return None
    params = {}
    for wanted, got in zip(route.segments, parts):
        if wanted.startswith('{') and wanted.endswith('}'):
            if not _ID.fullmatch(got):
                return None
            params[wanted[1:-1]] = got
        elif wanted != got:
            return None
    return params


def sse(name, payload):
    """One server-sent event. JSON escapes CR/LF, so data can never forge another field."""
    return b'event: ' + name.encode('ascii') + b'\ndata: ' + wire.dumps(payload) + b'\n\n'


class Stream:
    """A validated, authorised streaming response. Headers are final; the body is produced later.

    run() blocks (it uses the single Store worker), so an async adapter calls it in a thread and
    writes the staged `status` event and keep-alive comments while it runs.
    """
    def __init__(self, app, request, headers, produce):
        self.status, self.headers = 200, headers
        self._app, self._request, self._produce = app, request, produce

    def first(self):
        return sse('status', {'state': 'started'})

    def run(self):
        try:
            return [sse(name, payload) for name, payload in self._produce()]
        except WorkerBusy:
            code = 'busy'
        except PermissionError:
            code = 'not_found'
        except Exception as exc:
            self._app._log(logsafe.error_line(exc, self._request.request_id))
            return [sse('error', {'error': 'internal', 'request_id': self._request.request_id})]
        return [sse('error', {'error': code})]


# --- session routes -------------------------------------------------------------------------
def _cookie(sid, app, request, *, expire=False):
    parts = [f'{COOKIE}={"" if expire else sid}', 'HttpOnly', 'SameSite=Strict', 'Path=/',
             'Max-Age=0' if expire else f'Max-Age={int(app.sessions.absolute)}']
    if request.scheme == 'https':
        parts.append('Secure')
    return ('set-cookie', '; '.join(parts))


def login(request):
    app = request.app
    wait = app.throttle.check(request.peer)
    if wait:
        return 429, {'error': 'rate_limited'}, [('retry-after', str(wait))]
    token = request.json.get('token')

    def authenticate(backend):
        context = backend.auth.authenticate(token)
        _, role = backend.auth.reauthenticate(context.credential_id)
        return context.user_id, context.credential_id, role
    try:
        if not isinstance(token, str):
            raise PermissionError('Invalid credentials')
        user_id, credential_id, role = request.run(authenticate)
    except PermissionError:
        app.throttle.failure(request.peer)
        return 401, UNAUTHENTICATED
    app.throttle.success(request.peer)
    sid, session = app.sessions.create(credential_id, replace=request.sid)
    return (200, {'user_id': user_id, 'role': role, 'csrf': session.csrf},
            [_cookie(sid, app, request)])


def current_session(request):
    return 200, {'user_id': request.user_id, 'role': request.role, 'csrf': request.session.csrf}


def logout(request):
    request.app.sessions.invalidate(request.sid)
    return 200, {'status': 'logged_out'}, [_cookie('', request.app, request, expire=True)]


def session_routes():
    return [Route('POST', '/api/session', login, unsafe=True),
            Route('GET', '/api/session', current_session, private=True),
            Route('DELETE', '/api/session', logout, private=True, unsafe=True)]


# --- the application ------------------------------------------------------------------------
class App:
    def __init__(self, executor, pol, sessions, limiter, throttle, *, routes, log=lambda line: None):
        self._executor = executor
        self.policy = pol
        self.sessions = sessions
        self.limiter = limiter
        self.throttle = throttle
        self.routes = list(routes)
        self._log = log

    # public entry point ---------------------------------------------------------------------
    def handle(self, request):
        """Complete response. A Stream is collected into one text/event-stream body."""
        outcome = self.begin(request)
        if not isinstance(outcome, Stream):
            return outcome
        body = outcome.first() + b''.join(outcome.run())
        return Response(outcome.status, outcome.headers, body)

    def begin(self, request):
        """Run every check and the handler's validation. Returns a Response or a Stream."""
        started = time.perf_counter()
        request.app = self
        request.request_id = str(uuid4())
        try:
            route = self._dispatch_checks(request)
            status, payload, extra = self._execute(route, request)
            if route.stream:
                headers = ([('content-type', 'text/event-stream; charset=utf-8')]
                           + policy.response_headers(api=True)
                           + [('x-request-id', request.request_id)])
                self._log(logsafe.access_line(request.method, request.template, 200,
                                              (time.perf_counter() - started) * 1000,
                                              request.request_id))
                return Stream(self, request, headers, status)
        except PolicyError as exc:
            status, payload, extra = exc.status, {'error': exc.code}, []
        except wire.WireError as exc:
            status, payload, extra = exc.status, {'error': exc.code}, []
        except _Reply as exc:
            status, payload, extra = exc.status, exc.payload, exc.headers
        except WorkerBusy:
            status, payload, extra = 503, {'error': 'busy'}, []
        except PermissionError:
            status, payload, extra = 404, {'error': 'not_found'}, []
        except ValueError:
            status, payload, extra = 422, {'error': 'invalid_request'}, []
        except Exception as exc:
            self._log(logsafe.error_line(exc, request.request_id))
            status, payload, extra = 500, {'error': 'internal', 'request_id': request.request_id}, []
        headers = ([('content-type', 'application/json; charset=utf-8')]
                   + policy.response_headers(api=True)
                   + [('x-request-id', request.request_id)] + list(extra))
        self._log(logsafe.access_line(request.method, request.template, status,
                                      (time.perf_counter() - started) * 1000, request.request_id))
        return Response(status, headers, wire.dumps(payload))

    # steps ----------------------------------------------------------------------------------
    def _dispatch_checks(self, request):
        if len(request.body) > wire.MAX_BODY:
            raise PolicyError(413)
        request.headers = [(str(k).lower(), v) for k, v in request.headers]
        policy.check_headers(request.headers)
        if request.header('transfer-encoding') is not None:
            raise PolicyError(400)
        declared = request.header('content-length')
        if declared is not None and int(declared) != len(request.body):
            raise PolicyError(400)
        self.policy.check_host(request.header('host'))
        if len(request.query) > MAX_QUERY:
            raise _Reply(414, {'error': 'uri_too_long'})
        if not self.limiter.allow(request.peer):
            raise _Reply(429, {'error': 'rate_limited'},
                         [('retry-after', str(self.limiter.retry_after(request.peer)))])
        path = request.path
        candidates = []
        if isinstance(path, str) and path.startswith('/') and len(path) <= MAX_PATH:
            for route in self.routes:
                params = match_path(route, path)
                if params is not None:
                    candidates.append((route, params))
        if not candidates:
            raise _Reply(404, {'error': 'not_found'})
        for route, params in candidates:
            if route.method == request.method:
                request.params = params
                request.template = route.template
                return route
        raise _Reply(405, {'error': 'method_not_allowed'})

    def _execute(self, route, request):
        unsafe = route.unsafe or route.method not in SAFE_METHODS
        self.policy.check_origin(request.header('origin'), required=unsafe)
        if unsafe:
            self.policy.check_fetch_site(request.header('sec-fetch-site'))
        request.sid = self._cookie_sid(request)
        if route.private:
            self._authenticate(request)
            if unsafe and not self.sessions.check_csrf(request.session, request.header('x-csrf-token')):
                raise PolicyError(403)
            if route.operator and request.role != 'operator':
                raise _Reply(403, {'error': 'forbidden'})
        if unsafe and (request.body or route.method == 'POST'):
            if request.body:
                policy.check_content_type(request.header('content-type'))
            request.json = wire.loads(request.body)
        result = route.handler(request)
        if route.stream:
            return result, None, []
        status, payload = result[0], result[1]
        return status, payload, (result[2] if len(result) > 2 else [])

    @staticmethod
    def _cookie_sid(request):
        found = []
        for chunk in (request.header('cookie') or '').split(';'):
            name, _, value = chunk.strip().partition('=')
            if name == COOKIE:
                found.append(value)
        return found[0] if len(found) == 1 else None

    def _authenticate(self, request):
        session = self.sessions.lookup(request.sid)
        if session is None:
            raise _Reply(401, UNAUTHENTICATED)
        try:
            context, role = request.run(lambda backend: backend.auth.reauthenticate(session.credential_id))
        except PermissionError:
            self.sessions.invalidate_credential(session.credential_id)
            raise _Reply(401, UNAUTHENTICATED) from None
        request.session, request.context = session, context
        request.user_id, request.role = context.user_id, role


class _Reply(Exception):
    def __init__(self, status, payload, headers=()):
        super().__init__(status)
        self.status, self.payload, self.headers = status, payload, list(headers)
