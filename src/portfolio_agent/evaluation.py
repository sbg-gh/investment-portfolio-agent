"""Deterministic safety baseline. These public fixtures are not held-out learning evidence."""
from dataclasses import replace
import hashlib
from importlib.resources import files
import json
from .auth import Authenticator
from .core import FixtureValidator, Store, ValidatedSecurity
from .services import OperatorService, PortfolioService, POLICY_VERSION

SCENARIOS = {'cross_user_read','forged_identity','private_retrieval','null_owner',
             'public_promotion','live_order','outside_full_tier','public_fact'}

def load_cases():
    return json.loads(files('portfolio_agent').joinpath('eval_cases.json').read_text())

def _scenario(name):
    store = Store()
    try:
        for user in ('alice','bob','admin'):store.create_user(user)
        auth = Authenticator(store,clock=lambda:1000.0)
        contexts = {user:auth.authenticate(auth.provision(user,role='operator' if user=='admin' else 'user'))
                    for user in ('alice','bob','admin')}
        a,b,admin = (contexts[u] for u in ('alice','bob','admin'))
        validator = FixtureValidator({'NEW':ValidatedSecurity('new','NEW','mutual_fund','US','synthetic')})
        service = PortfolioService(store,auth,validator)
        operator = OperatorService(service)
        portfolio = service.create_portfolio(a,'fixture')
        try:
            if name == 'cross_user_read':
                service.holdings(b,portfolio)
            elif name == 'forged_identity':
                service.holdings(replace(b,user_id='alice'),portfolio)
            elif name == 'private_retrieval':
                service.add_memory(a,'episodic','private synthetic fact','fixture')
                return 'isolated' if service.memories(b)==[] else 'leaked'
            elif name == 'null_owner':
                store.add_private_memory(None,'episodic','private synthetic fact','fixture')
            elif name == 'public_promotion':
                operator.ingest_public_fact(a,'private synthetic fact','fixture')
            elif name == 'live_order':
                service.import_holding(a,portfolio,'NEW','1')
                service.record_paper_order(a,portfolio,'new','1','10',mode='live')
            elif name == 'outside_full_tier':
                for n in range(75):
                    with store.transaction():store.register(ValidatedSecurity(f's{n}',f'S{n}','stock','US','synthetic'))
                    operator.enable_monitoring(admin,f's{n}')
                service.import_holding(a,portfolio,'NEW','1')
                return 'accepted' if service.holdings(a,portfolio)==[('new','1')] else 'missing'
            elif name == 'public_fact':
                operator.ingest_public_fact(admin,'public synthetic fact','fixture')
                return 'shared' if service.memories(a)==service.memories(b)==[('semantic','public synthetic fact','fixture')] else 'missing'
        except PermissionError:
            return 'denied'
        return 'allowed'
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
        results.append({'id':case['id'],'expected':case['expected'],'observed':observed,
                        'passed':observed==case['expected']})
    return {'suite':'m1-safety-baseline-v1','policy_version':POLICY_VERSION,
            'dataset_sha256':hashlib.sha256(json.dumps(cases,sort_keys=True).encode()).hexdigest(),
            'total':len(results),'passed':sum(r['passed'] for r in results),'results':results}
