"""In-process client: dispatches requests through the framework-neutral web core (stdlib only)."""
import json as jsonlib
from datetime import datetime, timezone
from types import SimpleNamespace

from portfolio_agent.auth import Authenticator
from portfolio_agent.chat import ChatService
from portfolio_agent.core import Store
from portfolio_agent.model import ModelConfig, ScriptedModel
from portfolio_agent.registry_validator import RegistryValidator
from portfolio_agent.services import OperatorService, PortfolioService
from portfolio_agent.web.app import App, Request, Route, session_routes
from portfolio_agent.web.backend import Backend
from portfolio_agent.web.routes import default_routes
from portfolio_agent.web.policy import Policy
from portfolio_agent.web.ratelimit import LoginThrottle, RateLimiter
from portfolio_agent.web.sessions import SessionStore
from portfolio_agent.web.worker import Direct, Worker

PORT = 8765
HOST = f'127.0.0.1:{PORT}'
ORIGIN = f'http://{HOST}'
NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
SOURCE = 'operator-entered'


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class Reply:
    def __init__(self, response):
        self.status = response.status
        self.raw_headers = list(response.headers)
        self.headers = {}
        for name, value in response.headers:
            self.headers.setdefault(name, value)
        self.set_cookies = [v for n, v in response.headers if n == 'set-cookie']
        self.body = response.body

    def json(self):
        return jsonlib.loads(self.body)


class Client:
    def __init__(self, app, *, peer='127.0.0.1', scheme='http'):
        self.app, self.peer, self.scheme = app, peer, scheme
        self.cookies = {}
        self.csrf = None

    def request(self, method, path, *, json=None, body=None, headers=None, query='',
                cookies=True, origin=True, csrf=True, scheme=None):
        send = [('host', HOST), ('accept', 'application/json')]
        if method not in ('GET', 'HEAD'):
            if origin:
                send.append(('origin', ORIGIN))
            send.append(('content-type', 'application/json'))
        if csrf and self.csrf and method not in ('GET', 'HEAD'):
            send.append(('x-csrf-token', self.csrf))
        if cookies and self.cookies:
            send.append(('cookie', '; '.join(f'{k}={v}' for k, v in self.cookies.items())))
        payload = body if body is not None else (jsonlib.dumps(json).encode() if json is not None else b'')
        for name, value in (headers or {}).items():
            send = [h for h in send if h[0] != name.lower()]
            if value is not None:
                send.append((name.lower(), value))
        response = self.app.handle(Request(method, path, query, send, payload, self.peer,
                                           scheme or self.scheme))
        reply = Reply(response)
        for cookie in reply.set_cookies:
            name, _, rest = cookie.partition('=')
            value = rest.split(';')[0]
            if 'Max-Age=0' in cookie:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        return reply

    def login(self, token, **kwargs):
        reply = self.request('POST', '/api/session', json={'token': token}, **kwargs)
        if reply.status == 200:
            self.csrf = reply.json()['csrf']
        return reply


def make_env(extra_routes=(), *, full=False, replies=(), worker=False, **limits):
    """worker=True builds the Store on a dedicated thread (needed when the caller is threaded)."""
    clock = Clock()
    model = ScriptedModel(replies)
    built = {}

    def build():
        store = Store()
        for user in ('alice', 'bob', 'admin'):
            store.create_user(user)
        auth = Authenticator(store, clock=clock)
        built['tokens'] = {u: auth.provision(u, role='operator' if u == 'admin' else 'user',
                                             ttl_seconds=100000) for u in ('alice', 'bob', 'admin')}
        service = PortfolioService(store, auth, RegistryValidator(store))
        chat = ChatService(service, model, config=ModelConfig.from_env(env={}), clock=lambda: NOW,
                           valuation_source=SOURCE, on_model_error='facts')
        return Backend(store, auth, service, OperatorService(service), chat)
    if worker:
        executor = Worker(build)
        backend = None
    else:
        backend = build()
        executor = Direct(backend)
    sessions = SessionStore(clock=clock)
    logs = []
    routes = (list(default_routes()) if full else list(session_routes())) + list(extra_routes)
    app = App(executor, Policy(PORT), sessions,
              RateLimiter(clock=clock, capacity=limits.get('capacity', 1000), refill_per_second=1000),
              LoginThrottle(clock=clock, per_peer=limits.get('per_peer', 5), global_limit=limits.get('global_limit', 50)),
              routes=routes, log=logs.append)
    return SimpleNamespace(store=backend.store if backend else None,
                           auth=backend.auth if backend else None, tokens=built['tokens'],
                           sessions=sessions, clock=clock, app=app, logs=logs, model=model,
                           backend=backend, executor=executor, client=lambda **kw: Client(app, **kw))
