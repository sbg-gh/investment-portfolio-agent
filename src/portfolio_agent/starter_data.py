"""Starter data: the user's 20 ai-conviction symbols as operator-ATTESTED instruments plus a
clearly synthetic sample portfolio. Stdlib only. Never contains prices or observations; SKHY is
the user's own attestation, not verified by any authority.
"""
import json
from pathlib import Path
from .core import ValidatedSecurity
SAMPLE_PORTFOLIO_NAME = 'Sample portfolio (synthetic)'
SAMPLE_SOURCE = 'starter-sample-synthetic'
SAMPLE_KEY_PREFIX = 'starter-sample-v1:'
SAMPLE_QUANTITY = '100'
class StarterDataMissing(ValueError):
    """Some starter instruments are not registered; an operator must register them first."""
def load_starter_data():
    path = Path(__file__).with_name('starter_data.json')
    return json.loads(path.read_text(encoding='utf-8'))['entries']
def register_instruments(store):
    """Idempotently register the starter instruments as operator-attested. A symbol that is
    already registered (even by hand under another id) is counted as present and left unchanged.
    An id collision with a different symbol raises ValueError (Store.register). Trusted caller only.
    """
    registered = present = 0
    entries = load_starter_data()
    for entry in entries:
        if store.db.execute('SELECT 1 FROM securities WHERE symbol=?', (entry['symbol'],)).fetchone():
            present += 1
            continue
        store.register(ValidatedSecurity(entry['security_id'], entry['symbol'], entry['kind'],
                                         entry['country'], entry['source']))
        registered += 1
    return {'registered': registered, 'already_present': present, 'symbols': len(entries)}
def _existing_sample(store, user_id):
    row = store.db.execute(
        'SELECT p.id FROM portfolios p WHERE p.user_id=? AND p.name=? AND EXISTS ('
        'SELECT 1 FROM import_receipts r WHERE r.portfolio_id=p.id AND r.source=? '
        "AND substr(r.idempotency_key,1,?)=?) ORDER BY p.rowid LIMIT 1",
        (user_id, SAMPLE_PORTFOLIO_NAME, SAMPLE_SOURCE, len(SAMPLE_KEY_PREFIX),
         SAMPLE_KEY_PREFIX)).fetchone()
    return None if row is None else row[0]
def create_sample(store, user_id, validator, *, as_of):
    """Create (once) the caller's synthetic sample portfolio through the normal import path.
    All symbols are resolved first; if any is unregistered nothing is created. `user_id` must come
    from a trusted authentication context. Returns {'id','name','created'}.
    """
    existing = _existing_sample(store, user_id)
    if existing is not None:
        return {'id': existing, 'name': SAMPLE_PORTFOLIO_NAME, 'created': False}
    entries = load_starter_data()
    for entry in entries:
        try:
            validator.resolve(entry['symbol'])
        except ValueError:
            raise StarterDataMissing('Starter instruments are not registered') from None
    portfolio_id = store.create_portfolio(user_id, SAMPLE_PORTFOLIO_NAME)
    for entry in entries:
        store.import_holding_with_provenance(
            user_id, portfolio_id, entry['symbol'], SAMPLE_QUANTITY, validator,
            source=SAMPLE_SOURCE, market_as_of=as_of,
            idempotency_key=SAMPLE_KEY_PREFIX + entry['symbol'])
    return {'id': portfolio_id, 'name': SAMPLE_PORTFOLIO_NAME, 'created': True}
