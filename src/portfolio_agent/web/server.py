"""Operator CLI and server assembly: serve, create-user, issue-credential, revoke-credential.

Trusted local administration. Tokens are printed once at issuance and never logged; the database
stores only their hash. uvicorn is imported lazily and only by `serve`, so provisioning and the
zero-dependency gates never need the ui extra. The listener binds loopback unless two explicit
overrides are given (see bind.py).
"""
import argparse
import os
from pathlib import Path
import sqlite3
import sys
import json

from ..auth import Authenticator
from ..chat import ChatService
from ..core import Store
from ..model import BoundedModel, ModelConfig, OpenAICompatibleModel
from ..registry_validator import RegistryValidator
from ..services import OperatorService, PortfolioService
from .app import App
from .asgi import AsgiApp, KEEPALIVE_SECONDS, load_static
from .backend import Backend
from .bind import BindRefused, REMOTE_ACK, REMOTE_ENV, resolve_bind
from .policy import Policy
from .ratelimit import LoginThrottle, RateLimiter
from .routes import default_routes
from .sessions import SessionStore
from .worker import Worker

DEFAULT_PORT = 8765
DEFAULT_SOURCE = 'operator-entered'
STATIC_DIR = Path(__file__).resolve().parent / 'static'
MAX_TTL_HOURS = 24 * 90


class _NoModel:
    """Stands in when no model is configured: chat degrades to deterministic facts."""
    def generate(self, prompt, *, max_tokens):
        raise RuntimeError('no model configured')


def _ttl_hours(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError('ttl must be a number of hours') from None
    if not 0 < value <= MAX_TTL_HOURS:
        raise argparse.ArgumentTypeError(f'ttl must be between 0 and {MAX_TTL_HOURS} hours')
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='portfolio_agent.web', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='run the local web UI (loopback only by default)')
    serve.add_argument('--db', required=True, help='path to the sqlite database')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=DEFAULT_PORT)
    serve.add_argument('--allow-non-loopback', action='store_true',
                       help=f'also requires {REMOTE_ENV}={REMOTE_ACK} and --allowed-host; there is no TLS')
    serve.add_argument('--allowed-host', action='append', default=[],
                       help='extra Host header value to accept, e.g. myhost:8765 (repeatable)')
    serve.add_argument('--model-from-env', action='store_true',
                       help='use the OpenAI-compatible model from MODEL_ENDPOINT / MODEL_NAME')
    serve.add_argument('--model-timeout', type=float, default=30.0,
                       help='seconds to wait for the model; a slow model blocks other requests')
    serve.add_argument('--valuation-source', default=DEFAULT_SOURCE,
                       help='observation source label chat uses for valuation context')
    create = commands.add_parser('create-user', help='create a user identity')
    create.add_argument('user_id')
    create.add_argument('--db', required=True)
    issue = commands.add_parser('issue-credential', help='print a new bearer token once')
    issue.add_argument('user_id')
    issue.add_argument('--db', required=True)
    issue.add_argument('--role', choices=('user', 'operator'), default='user')
    issue.add_argument('--ttl-hours', type=_ttl_hours, default=24.0)
    revoke = commands.add_parser('revoke-credential', help='revoke a credential by id')
    revoke.add_argument('credential_id')
    revoke.add_argument('--db', required=True)
    return parser.parse_args(argv)


def open_auth(db):
    store = Store(db)
    return store, Authenticator(store)


# --- assembly ------------------------------------------------------------------------------
def _backend(db, model, config, valuation_source):
    store = Store(db)
    auth = Authenticator(store)
    service = PortfolioService(store, auth, RegistryValidator(store))
    chat = ChatService(service, model or _NoModel(), config=config, valuation_source=valuation_source,
                       on_model_error='facts')
    return Backend(store, auth, service, OperatorService(service), chat)


def build_asgi(db, *, port, model, valuation_source, static_dir=STATIC_DIR, allowed_hosts=(),
               config=None, keepalive=KEEPALIVE_SECONDS, log=lambda line: print(line, flush=True)):
    config = config or ModelConfig.from_env(env={})
    worker = Worker(lambda: _backend(db, model, config, valuation_source))
    app = App(worker, Policy(port, allowed_hosts), SessionStore(), RateLimiter(), LoginThrottle(),
              routes=default_routes(), log=log)
    return AsgiApp(app, static=load_static(static_dir), keepalive=keepalive, on_shutdown=worker.close)


def build_server(db, *, host, port, model, valuation_source, static_dir=STATIC_DIR,
                 allowed_hosts=(), config=None, keepalive=KEEPALIVE_SECONDS):
    import uvicorn
    asgi = build_asgi(db, port=port, model=model, valuation_source=valuation_source,
                      static_dir=static_dir, allowed_hosts=allowed_hosts, config=config,
                      keepalive=keepalive)
    # Transport only: no access log, no Server header, no proxy header trust, bounded headers
    # and concurrency, no websockets. Our own payload-free log line is the only request log.
    settings = uvicorn.Config(asgi, host=host, port=port, access_log=False, server_header=False,
                              proxy_headers=False, log_config=None, log_level='warning',
                              http='h11', ws='none', lifespan='on', limit_concurrency=64,
                              timeout_keep_alive=5, backlog=64, h11_max_incomplete_event_size=16384)
    return uvicorn.Server(settings), asgi


# --- commands ------------------------------------------------------------------------------
def _serve(args, env):
    host, port = resolve_bind(args.host, args.port, args.allow_non_loopback, env, args.allowed_host)
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("error: serve needs the optional ui extra: pip install -e '.[ui]'", file=sys.stderr)
        return 2
    config = ModelConfig.from_env(env=env)
    model = None
    if args.model_from_env:
        model = BoundedModel(OpenAICompatibleModel(config, timeout=args.model_timeout), config)
    server, _ = build_server(args.db, host=host, port=port, model=model,
                             valuation_source=args.valuation_source,
                             allowed_hosts=tuple(args.allowed_host), config=config)
    shown = f'[{host}]' if ':' in host else host
    if host in ('127.0.0.1', '::1'):
        print(f'Serving on http://{shown}:{port} (loopback only, no TLS). Ctrl+C to stop.', flush=True)
    else:
        print('WARNING: NON-LOOPBACK LISTENER WITHOUT TLS. Anyone who can reach this address can '
              f'attempt to log in. Serving on http://{shown}:{port}', flush=True)
    server.run()
    return 0


def _create_user(args, env):
    store = Store(args.db)
    try:
        store.create_user(args.user_id)
    finally:
        store.close()
    print(json.dumps({'user_id': args.user_id}))
    return 0


def _issue(args, env):
    store, auth = open_auth(args.db)
    try:
        ttl = int(args.ttl_hours * 3600)
        token = auth.provision(args.user_id, role=args.role, ttl_seconds=ttl)
        credential = auth.authenticate(token).credential_id
    finally:
        store.close()
    print('Store this token now; it is not shown again and only its hash is kept.', file=sys.stderr)
    print(json.dumps({'user_id': args.user_id, 'role': args.role, 'credential_id': credential,
                      'expires_in_seconds': ttl, 'token': token}))
    return 0


def _revoke(args, env):
    store, auth = open_auth(args.db)
    try:
        auth.revoke(args.credential_id)
    finally:
        store.close()
    print(json.dumps({'credential_id': args.credential_id, 'revoked': True}))
    return 0


COMMANDS = {'serve': _serve, 'create-user': _create_user, 'issue-credential': _issue,
            'revoke-credential': _revoke}


def main(argv=None, env=None):
    env = os.environ if env is None else env
    args = parse_args(argv)
    try:
        return COMMANDS[args.command](args, env)
    except BindRefused as exc:
        print(f'error: {exc}', file=sys.stderr)
    except PermissionError:
        print('error: unknown user or credential', file=sys.stderr)
    except sqlite3.IntegrityError:
        print('error: already exists', file=sys.stderr)
    except (ValueError, sqlite3.Error):
        print('error: invalid request or unusable database', file=sys.stderr)
    return 2 if args.command == 'serve' else 1
