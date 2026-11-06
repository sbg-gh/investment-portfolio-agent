"""Offline demonstrations; no network listener or production credentials."""
import argparse
import json
from .auth import Authenticator
from .core import FixtureValidator, Store, ValidatedSecurity
from .services import PortfolioService

def demo():
    store = Store()
    try:
        store.create_user('demo-user')
        auth = Authenticator(store)
        context = auth.authenticate(auth.provision('demo-user'))
        validator = FixtureValidator({'DEMO': ValidatedSecurity('fixture:demo','DEMO','etf','US','synthetic-fixture')})
        service = PortfolioService(store,auth,validator)
        portfolio = service.create_portfolio(context,'Example portfolio')
        service.import_holding(context,portfolio,'DEMO','2.5')
        service.add_memory(context,'episodic','Fixture import succeeded','demo-run')
        print(json.dumps({'mode':'read_only','holdings':service.holdings(context,portfolio),
                          'memory_count':len(service.memories(context)),
                          'trace_count':len(service.traces(context))},indent=2))
    finally:
        store.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['demo','eval','eval-valuation'])
    args = parser.parse_args()
    if args.command == 'demo':
        demo()
    else:
        if args.command == 'eval':
            from .evaluation import evaluate as evaluate_safety
            from .import_evaluation import evaluate as evaluate_import
            reports = [evaluate_safety(), evaluate_import()]
        else:
            from .valuation_evaluation import evaluate
            reports = [evaluate()]
        print(json.dumps(reports if len(reports)>1 else reports[0],indent=2))
        if any(report['passed'] != report['total'] for report in reports):
            raise SystemExit(1)

if __name__ == '__main__':
    main()
