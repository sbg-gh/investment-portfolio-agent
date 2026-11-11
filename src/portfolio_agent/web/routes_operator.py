"""Operator-only endpoints: attested instrument registration and manual observation entry.

Instruments are operator-ATTESTED, not authoritatively validated. Observations are entered by
hand; there is no live market data feed.
"""
from decimal import Decimal

from . import routes_common as rc
from .app import Route
from .wire import WireError
from ..ports import Observation


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


def operator_routes():
    return [Route('POST', '/api/operator/instruments', register_instrument, private=True,
                  unsafe=True, operator=True),
            Route('POST', '/api/operator/observations', add_observation, private=True,
                  unsafe=True, operator=True)]
