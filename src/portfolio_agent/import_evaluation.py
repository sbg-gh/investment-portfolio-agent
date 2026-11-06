"""Deterministic import-provenance regression fixtures. Not held-out learning evidence."""
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
from .auth import Authenticator
from .core import FixtureValidator, Store, ValidatedSecurity
from .services import PortfolioService, POLICY_VERSION

SCENARIOS = {'explicit_removal', 'import_receipt', 'effective_dated_alias', 'idempotent_retry'}

AS_OF = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
ALIAS_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load_cases():
    return json.loads(files('portfolio_agent').joinpath('import_cases.json').read_text())


def _scenario(name):
    store = Store()
    try:
        store.create_user('fixture-user')
        auth = Authenticator(store, clock=lambda: 1000.0)
        ctx = auth.authenticate(auth.provision('fixture-user'))
        validator = FixtureValidator({'SYN': ValidatedSecurity('syn0', 'SYN', 'stock', 'US', 'synthetic')})
        app = PortfolioService(store, auth, validator)
        portfolio = app.create_portfolio(ctx, 'synthetic')
        if name == 'import_receipt':
            app.import_holding_with_provenance(ctx, portfolio, 'SYN', '1.5',
                source='synthetic-feed', market_as_of=AS_OF, idempotency_key='r1')
            receipts = app.receipts(ctx, portfolio)
            if len(receipts) != 1:
                return 'missing'
            _id, security_id, source, market_as_of, quantity, action = receipts[0]
            return ('receipt_recorded' if source == 'synthetic-feed'
                    and market_as_of == AS_OF.isoformat() and action == 'upsert' else 'missing')
        if name == 'explicit_removal':
            app.import_holding_with_provenance(ctx, portfolio, 'SYN', '1.5',
                source='synthetic-feed', market_as_of=AS_OF, idempotency_key='r1')
            app.remove_holding(ctx, portfolio, 'syn0',
                source='synthetic-feed', idempotency_key='rm1', market_as_of=AS_OF)
            removed = app.holdings(ctx, portfolio) == []
            audited = any(r[5] == 'remove' for r in app.receipts(ctx, portfolio))
            return 'removed' if removed and audited else 'retained'
        if name == 'effective_dated_alias':
            with store.db:
                store.register(ValidatedSecurity('aliased', 'REAL', 'stock', 'US', 'synthetic'))
            store.register_alias('GHOST', 'aliased', 'synthetic', ALIAS_FROM)
            # Validator does NOT know GHOST; resolution must bind to the aliased security.
            security_id = app.import_holding_with_provenance(ctx, portfolio, 'GHOST', '2',
                source='synthetic-feed', market_as_of=AS_OF, idempotency_key='a1')
            return 'alias_resolved' if security_id == 'aliased' else 'misresolved'
        if name == 'idempotent_retry':
            app.import_holding_with_provenance(ctx, portfolio, 'SYN', '1.5',
                source='synthetic-feed', market_as_of=AS_OF, idempotency_key='k1')
            app.import_holding_with_provenance(ctx, portfolio, 'SYN', '9',
                source='synthetic-feed', market_as_of=AS_OF, idempotency_key='k1')
            one_receipt = len(app.receipts(ctx, portfolio)) == 1
            unchanged = app.holdings(ctx, portfolio) == [('syn0', '1.5')]
            return 'single_apply' if one_receipt and unchanged else 'double_applied'
        return 'unknown'
    finally:
        store.close()


def evaluate(cases=None):
    cases = load_cases() if cases is None else cases
    if not cases or len({case['id'] for case in cases}) != len(cases):
        raise ValueError('Evaluation cases must be nonempty with unique IDs')
    if any(case['scenario'] not in SCENARIOS for case in cases):
        raise ValueError('Unknown evaluation scenario')
    results = []
    for case in cases:
        observed = _scenario(case['scenario'])
        results.append({'id': case['id'], 'expected': case['expected'], 'observed': observed,
                        'passed': observed == case['expected']})
    return {'suite': 'm2-import-provenance-baseline-v1', 'policy_version': POLICY_VERSION,
            'dataset_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            'total': len(results), 'passed': sum(r['passed'] for r in results), 'results': results}
