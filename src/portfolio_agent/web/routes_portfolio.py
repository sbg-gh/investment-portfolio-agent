"""Portfolio, holdings, receipts, valuation and public lookup endpoints.

Identity comes only from the server-side session (request.context). Path ids are opaque
strings checked by the service layer; a foreign id is indistinguishable from a missing one.
Money and quantities are decimal strings.
"""
from datetime import timedelta
from uuid import uuid4

from . import routes_common as rc
from .app import Route
from .wire import WireError
from ..starter_data import StarterDataMissing
from ..valuation import FreshnessPolicy

SOURCE_LABEL = 'web-ui'
MAX_IDS = 500


def _security_index(request, ids):
    found = request.run(lambda b: b.service.securities(request.context, sorted(set(ids))))
    return {item['security_id']: item for item in found}


def list_portfolios(request):
    return 200, request.run(lambda b: b.service.portfolios(request.context))


def create_portfolio(request):
    name = rc.text(request.json.get('name'), max_len=100)
    pid = request.run(lambda b: b.service.create_portfolio(request.context, name))
    return 201, {'id': pid, 'name': name}


def create_sample_portfolio(request):
    # The owner is the session user only; any body (including a user_id) is ignored.
    try:
        result = request.run(lambda b: b.service.create_sample_portfolio(
            request.context, as_of=rc.now()))
    except StarterDataMissing:
        return 409, {'error': 'starter_instruments_missing'}
    return (201 if result['created'] else 200), result
def list_holdings(request):
    pid = request.params['id']
    rows = request.run(lambda b: b.service.holdings(request.context, pid))
    index = _security_index(request, [row[0] for row in rows]) if rows else {}
    return 200, [{'security_id': sid, 'symbol': index.get(sid, {}).get('symbol'),
                  'kind': index.get(sid, {}).get('kind'), 'quantity': quantity}
                 for sid, quantity in rows]


def import_holding(request):
    pid, body = request.params['id'], request.json
    symbol = rc.symbol(body.get('symbol'))
    quantity = rc.decimal(body.get('quantity'), 'quantity')
    key = rc.idempotency_key(body.get('idempotency_key'))
    security_id = request.run(lambda b: b.service.import_holding_with_provenance(
        request.context, pid, symbol, quantity, source=SOURCE_LABEL, market_as_of=rc.now(),
        idempotency_key=key))
    return 200, {'security_id': security_id}


def remove_holding(request):
    pid, sid = request.params['id'], request.params['security_id']
    key = (rc.idempotency_key(request.json.get('idempotency_key')) if request.body
           else uuid4().hex)
    removed = request.run(lambda b: b.service.remove_holding(
        request.context, pid, sid, source=SOURCE_LABEL, idempotency_key=key, market_as_of=rc.now()))
    return 200, {'security_id': removed}


def list_receipts(request):
    pid = request.params['id']
    rows = request.run(lambda b: b.service.receipts(request.context, pid))
    return 200, [dict(zip(('id', 'security_id', 'source', 'market_as_of', 'quantity', 'action'), row))
                 for row in rows]


def valuation(request):
    pid = request.params['id']
    params = rc.query(request, {'source', 'as_of', 'max_age_days'})
    source = rc.text(params.get('source'), max_len=64)
    as_of = rc.timestamp(params['as_of']) if 'as_of' in params else rc.now()
    policy = None  # absent: the unchanged default (price 24 h, NAV 72 h)
    if 'max_age_days' in params:
        days = rc.window_days(params['max_age_days'])
        # The NAV window is never tightened below its 3-day default. Built per request only.
        policy = FreshnessPolicy(price_max_age=timedelta(days=days),
                                 nav_max_age=timedelta(days=max(days, 3)))
    report = request.run(lambda b: b.service.value_portfolio(
        request.context, pid, as_of=as_of, source=source, policy=policy))
    # Window lengths are whole seconds on the wire (no floats anywhere).
    report['policy'] = {name: int(value) for name, value in report['policy'].items()}
    return 200, report


def securities(request):
    raw = rc.query(request, {'ids'}).get('ids')
    if not raw:
        raise WireError('invalid_query')
    ids = raw.split(',')
    if len(ids) > MAX_IDS:
        raise WireError('invalid_query')
    return 200, request.run(lambda b: b.service.securities(request.context, ids))


def sources(request):
    return 200, request.run(lambda b: b.service.observation_sources(request.context))


def portfolio_routes():
    base = '/api/portfolios'
    return [Route('GET', base, list_portfolios, private=True),
            Route('POST', base, create_portfolio, private=True, unsafe=True),
            Route('POST', '/api/starter/sample-portfolio', create_sample_portfolio, private=True,
                  unsafe=True),
            Route('GET', base + '/{id}/holdings', list_holdings, private=True),
            Route('POST', base + '/{id}/holdings', import_holding, private=True, unsafe=True),
            Route('DELETE', base + '/{id}/holdings/{security_id}', remove_holding, private=True,
                  unsafe=True),
            Route('GET', base + '/{id}/receipts', list_receipts, private=True),
            Route('GET', base + '/{id}/valuation', valuation, private=True),
            Route('GET', '/api/securities', securities, private=True),
            Route('GET', '/api/sources', sources, private=True)]
