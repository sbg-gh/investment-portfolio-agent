"""Operator CLI and server assembly: serve, create-user, issue-credential, revoke-credential,
seed-starter-data, import-prices, fetch-prices.

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
from .fetch_manager import FetchManager
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


MODEL_PREAMBLE = ('Reply with ONLY one JSON object and nothing else, exactly of the form '
                  '{"explanation": "<short plain-text answer>", "tool_calls": []}. '
                  'Always use an empty "tool_calls": [] list.')
def preamble_for(model):
    """Opt-in format hint for a real model only; offline/no-model servers keep the M4 prompt unchanged."""
    return None if model is None else MODEL_PREAMBLE
class _NoModel:
    """Stands in when no model is configured: chat degrades to deterministic facts."""
    def generate(self, prompt, *, max_tokens):
        raise RuntimeError('no model configured')


def _print_line(line):
    print(line, flush=True)


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
    seed = commands.add_parser('seed-starter-data', help='register the starter instruments '
                               '(and with --user, that user\'s synthetic sample portfolio)')
    seed.add_argument('--db', required=True)
    seed.add_argument('--user', default=None, help='existing user id who owns the sample portfolio '
                      '(trusted local administration; there is no authentication context here)')
    prices = commands.add_parser('import-prices', help='import a CSV of closing prices '
                                 '(symbol,date,close) as observations; trusted local administration')
    prices.add_argument('--db', required=True)
    prices.add_argument('--csv', required=True, help='path to the CSV file (UTF-8, up to 1 MiB)')
    prices.add_argument('--source', required=True, help='observation source name for the whole file')
    prices.add_argument('--dry-run', action='store_true', help='validate and report, write nothing')
    fetch = commands.add_parser('fetch-prices', help='fetch end-of-day closes for the registered stocks and '
                                'ETFs from Yahoo Finance (unofficial; manual; trusted local administration)')
    fetch.add_argument('--db', required=True)
    fetch.add_argument('--dry-run', action='store_true', help='fetch and validate, write nothing')
    return parser.parse_args(argv)


def open_auth(db):
    store = Store(db)
    return store, Authenticator(store)


# --- assembly ------------------------------------------------------------------------------
def _backend(db, model, config, valuation_source, prompt_preamble=None):
    store = Store(db)
    auth = Authenticator(store)
    service = PortfolioService(store, auth, RegistryValidator(store))
    chat = ChatService(service, model or _NoModel(), config=config, valuation_source=valuation_source,
                       prompt_preamble=prompt_preamble, on_model_error='facts')
    return Backend(store, auth, service, OperatorService(service), chat)


def build_asgi(db, *, port, model, valuation_source, static_dir=STATIC_DIR, allowed_hosts=(),
               config=None, keepalive=KEEPALIVE_SECONDS, log=None, fetch_options=None):
    config = config or ModelConfig.from_env(env={})
    log = _print_line if log is None else log
    worker = Worker(lambda: _backend(db, model, config, valuation_source, preamble_for(model)))
    fetch = FetchManager(worker, **(fetch_options or {}))
    app = App(worker, Policy(port, allowed_hosts), SessionStore(), RateLimiter(), LoginThrottle(),
              routes=default_routes(), log=log, fetch=fetch)
    def shutdown():
        fetch.close()    # stop the network thread first, then the store worker it may be using
        worker.close()
    return AsgiApp(app, static=load_static(static_dir), keepalive=keepalive, on_shutdown=shutdown)


def build_server(db, *, host, port, model, valuation_source, static_dir=STATIC_DIR,
                 allowed_hosts=(), config=None, keepalive=KEEPALIVE_SECONDS, log=None):
    import uvicorn
    asgi = build_asgi(db, port=port, model=model, valuation_source=valuation_source,
                      static_dir=static_dir, allowed_hosts=allowed_hosts, config=config,
                      keepalive=keepalive, log=log)
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


def _seed_starter(args, env):
    from datetime import datetime, timezone
    from .. import starter_data
    store = Store(args.db)
    try:
        with store.transaction():
            if args.user is not None and not store.db.execute(
                    'SELECT 1 FROM users WHERE id=?', (args.user,)).fetchone():
                raise PermissionError('unknown user')
            counts = starter_data.register_instruments(store)
            sample = 'skipped'
            if args.user is not None:
                made = starter_data.create_sample(store, args.user, RegistryValidator(store),
                                                  as_of=datetime.now(timezone.utc))
                sample = 'created' if made['created'] else 'existing'
    finally:
        store.close()
    print(json.dumps({'instruments_registered': counts['registered'],
                      'instruments_already_present': counts['already_present'],
                      'sample_portfolio': sample}))
    return 0


def _import_prices(args, env):
    """Exit 0: processed, nothing rejected. 3: processed with rejected rows. 1: file-level error."""
    from datetime import datetime, timezone
    from .. import price_import
    try:
        with open(args.csv, 'rb') as handle:
            data = handle.read(price_import.MAX_BYTES + 1)
    except OSError:
        print('error: invalid_file', file=sys.stderr)
        return 1
    store = Store(args.db)
    try:
        result = price_import.import_prices(store, data, args.source, dry_run=args.dry_run,
                                            now=datetime.now(timezone.utc))
    except price_import.FileError as exc:
        print(f'error: {exc.code}', file=sys.stderr)
        return 1
    finally:
        store.close()
    print(json.dumps(result))
    accepted = sum(result['counts'].get(code, 0) for code in ('imported', 'unchanged', 'corrected'))
    return 0 if accepted == result['total_rows'] else 3


def _fetch_prices(args, env):
    """Exit 0: every symbol accepted. 3: some symbol failed or was skipped. 1: configuration error.

    Unofficial source; automated retrieval is outside Yahoo's terms and was accepted by the operator
    (docs/DECISIONS.md). Only ticker symbols and the fixed range leave the machine."""
    from datetime import datetime, timezone
    from .. import price_fetch, price_import
    hooks = dict(price_fetch.HOOKS)
    clock = hooks.get('clock') or (lambda: datetime.now(timezone.utc))
    store = Store(args.db)
    try:
        targets = price_fetch.read_targets(store)
        if not targets:
            print('error: no_instruments', file=sys.stderr)
            return 1
        result = price_fetch.run_press(
            targets, lambda rows, dry: price_import.import_rows(
                store, rows, price_fetch.SOURCE, dry_run=dry, now=clock()),
            dry_run=args.dry_run, **hooks)
    finally:
        store.close()
    print(json.dumps(result))
    accepted = sum(result['counts'].get(code, 0) for code in price_fetch.ACCEPTED)
    return 0 if accepted == len(result['symbols']) else 3
COMMANDS = {'serve': _serve, 'create-user': _create_user, 'issue-credential': _issue,
            'revoke-credential': _revoke, 'seed-starter-data': _seed_starter,
            'import-prices': _import_prices, 'fetch-prices': _fetch_prices}


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
