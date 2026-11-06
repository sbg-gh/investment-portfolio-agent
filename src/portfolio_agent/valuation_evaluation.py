"""Public synthetic valuation correctness fixtures, not investment or learning results."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from importlib.resources import files
import json
from .auth import Authenticator
from .core import Store, FixtureValidator, ValidatedSecurity
from .ports import Observation
from .services import PortfolioService, OperatorService
from .valuation import VALUATION_VERSION

CUTOFF = datetime(2026,11,6,21,tzinfo=timezone.utc)

def load_cases():
    return json.loads(files('portfolio_agent').joinpath('valuation_cases.json').read_text())

def _case(case):
    store = Store()
    try:
        store.create_user('fixture-user');store.create_user('fixture-operator')
        auth = Authenticator(store)
        user = auth.authenticate(auth.provision('fixture-user'))
        operator = auth.authenticate(auth.provision('fixture-operator',role='operator'))
        records = {symbol:ValidatedSecurity(symbol,symbol,kind,'US','synthetic')
                   for symbol,kind,_ in case['holdings']}
        app = PortfolioService(store,auth,FixtureValidator(records));ops = OperatorService(app)
        portfolio = app.create_portfolio(user,'synthetic')
        for symbol,_,quantity in case['holdings']:
            app.import_holding(user,portfolio,symbol,quantity)
        for symbol,value,basis,age,fetch_age in case['observations']:
            ops.ingest_observation(operator,Observation(symbol,Decimal(value),CUTOFF-timedelta(seconds=age),
                'fixture',basis,fetched_at=CUTOFF-timedelta(seconds=fetch_age)))
        report = app.value_portfolio(user,portfolio,as_of=CUTOFF,source='fixture')
        return {'status':report['status'],'total':report['total']}
    finally:
        store.close()

def evaluate(cases=None):
    cases = load_cases() if cases is None else cases
    if not cases or len({c['id'] for c in cases})!=len(cases):
        raise ValueError('Nonempty cases with unique IDs required')
    results = []
    for case in cases:
        observed = _case(case)
        results.append({'id':case['id'],'expected':case['expected'],'observed':observed,
                        'passed':observed==case['expected']})
    return {'suite':'m2-valuation-baseline-v1','valuation_version':VALUATION_VERSION,
            'dataset_sha256':hashlib.sha256(json.dumps(cases,sort_keys=True).encode()).hexdigest(),
            'total':len(results),'passed':sum(r['passed'] for r in results),'results':results}
