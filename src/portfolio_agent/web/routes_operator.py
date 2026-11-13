"""Operator-only endpoints: attested instrument registration, manual observation entry, CSV
price import and the manual Yahoo end-of-day fetch (background, see fetch_manager.py).

Instruments are operator-ATTESTED, not authoritatively validated. Observations are entered by
hand; there is no live market data feed.
"""
from decimal import Decimal

from . import routes_common as rc
from .app import Route
from .wire import WireError
from ..ports import Observation
from ..price_import import FileError


def register_instrument(request):
    body = request.json
    security_id = rc.text(body.get('security_id'), max_len=64)
    symbol = rc.symbol(body.get('symbol'))
    kind = rc.text(body.get('kind'), max_len=32)
    registered = request.run(lambda b: b.ops.register_instrument(
        request.context, security_id=security_id, symbol=symbol, kind=kind))
    return 200, {'security_id': registered}


def add_observation(request):
    body = request.json
    basis = body.get('basis')
    if basis not in ('price', 'nav'):
        raise WireError('invalid_field')
    observed = rc.timestamp(body.get('observed_at'))
    fetched = rc.timestamp(body['fetched_at']) if 'fetched_at' in body else rc.now()
    observation = Observation(rc.text(body.get('security_id'), max_len=64),
                              Decimal(rc.decimal(body.get('value'), 'value')), observed,
                              rc.text(body.get('source'), max_len=64), basis, fetched_at=fetched)
    identity = request.run(lambda b: b.ops.ingest_observation(request.context, observation))
    return 200, {'observation_id': identity}


def import_prices(request):
    body = request.json
    source, csv_text, dry_run = body.get('source'), body.get('csv_text'), body.get('dry_run')
    if not isinstance(source, str) or not isinstance(csv_text, str) or not isinstance(dry_run, bool):
        raise WireError('invalid_field')
    try:
        result = request.run(lambda b: b.ops.import_prices(
            request.context, source=source, csv_text=csv_text, dry_run=dry_run))
    except FileError as exc:
        raise WireError(exc.code) from None  # only the fixed code leaves; never file content
    return 200, result


def _manager(request):
    manager = request.app.fetch
    if manager is None:
        raise WireError('unavailable', 503)
    return manager
def start_fetch(request):
    dry_run = request.json.get('dry_run')
    if not isinstance(dry_run, bool):
        raise WireError('invalid_field')
    # Identity comes only from the session context; nothing else in the body is read.
    fetch_id = _manager(request).start(request.user_id, request.context.credential_id, dry_run)
    return 202, {'fetch_id': fetch_id}
def fetch_status(request):
    return 200, _manager(request).status(request.user_id, request.params['fetch_id'])
def register_starter_instruments(request):
    # No body is read: the symbol list is fixed package data, never client supplied.
    return 200, request.run(lambda b: b.ops.register_starter_instruments(request.context))
def operator_routes():
    return [Route('POST', '/api/operator/starter/instruments', register_starter_instruments,
                  private=True, unsafe=True, operator=True),
            Route('POST', '/api/operator/instruments', register_instrument, private=True,
                  unsafe=True, operator=True),
            Route('POST', '/api/operator/observations', add_observation, private=True,
                  unsafe=True, operator=True),
            Route('POST', '/api/operator/prices/import', import_prices, private=True,
                  unsafe=True, operator=True),
            Route('POST', '/api/operator/prices/fetch', start_fetch, private=True,
                  unsafe=True, operator=True),
            Route('GET', '/api/operator/prices/fetch/{fetch_id}', fetch_status, private=True,
                  operator=True)]
