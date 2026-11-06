"""Offline demonstrations; no network listener or production credentials."""
import argparse
from datetime import datetime, timezone
import json
from .auth import Authenticator
from .core import FixtureValidator, Store, ValidatedSecurity
from .jobs import JobRunner, build_default_handlers
from .notifier import LocalSinkNotifier
from .services import PortfolioService
from . import monitoring

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
        # Proactive service: enqueue one owner-scoped monitor job and drive exactly one
        # deterministic tick (no background thread, no real channel). The owner is
        # re-authorized at execution time; the summary stays payload-free.
        now = datetime(2026,1,2,15,tzinfo=timezone.utc)
        runner = JobRunner(store, build_default_handlers(store))
        runner.jobs.enqueue('monitor_portfolio', idempotency_key=f'mp|{portfolio}',
                            due_at=now, correlation_id='demo-monitor',
                            owner_ref=monitoring.encode_owner_ref(context.user_id, portfolio))
        claimed = runner.tick(now)
        notifier = LocalSinkNotifier()
        delivered = store.deliver_pending(notifier, now)
        jobs_done = store.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='done'").fetchone()[0]
        outbox_count = store.db.execute(
            "SELECT COUNT(*) FROM notification_outbox").fetchone()[0]
        print(json.dumps({'mode':'read_only','holdings':service.holdings(context,portfolio),
                          'memory_count':len(service.memories(context)),
                          'trace_count':len(service.traces(context)),
                          'jobs_claimed':len(claimed),'jobs_done':jobs_done,
                          'outbox_count':outbox_count,'notifications_delivered':delivered},indent=2))
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
            from .m2_evaluation import evaluate as evaluate_m2
            from .m3_evaluation import evaluate as evaluate_m3
            reports = [evaluate_safety(), evaluate_import(), evaluate_m2(), evaluate_m3()]
        else:
            from .valuation_evaluation import evaluate
            reports = [evaluate()]
        print(json.dumps(reports if len(reports)>1 else reports[0],indent=2))
        if any(report['passed'] != report['total'] for report in reports):
            raise SystemExit(1)

if __name__ == '__main__':
    main()
