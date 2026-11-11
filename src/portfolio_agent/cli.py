"""Offline demonstrations; no network listener or production credentials."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from .auth import Authenticator
from .core import FixtureValidator, Store, ValidatedSecurity
from .jobs import JobRunner, build_default_handlers
from .notifier import LocalSinkNotifier
from .ports import Observation
from .services import OperatorService, PortfolioService
from .chat import ChatService
from .model import ModelConfig, ScriptedModel
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
        # M4 hybrid chat: one offline, READ-ONLY turn over an injectable fake model. The
        # model only PROPOSES; the deterministic schema+authz gate re-checks before any read.
        # No network, no live server. The summary stays payload-free (counts only).
        scripted = ScriptedModel([json.dumps({
            'explanation': 'Here is a read-only summary of your portfolio.',
            'tool_calls': [{'name': 'value_portfolio',
                            'args': {'portfolio_id': portfolio, 'as_of': now.isoformat(),
                                     'source': 'synthetic-fixture'}}]})])
        chat = ChatService(service, scripted, config=ModelConfig.from_env(env={}),
                           clock=lambda: now)
        chat_out = chat.chat(context, conversation_id='demo-conversation',
                             message='what is in my portfolio?', portfolio_id=portfolio)
        # M5a memory substrate (SUBSTRATE ONLY, no learning/adaptation claim): capture one
        # owner-scoped run record (secrets redacted, tool names only) and retrieve scoped
        # memories by a keyword. The summary stays payload-free — counts only, no bodies,
        # symbols or payloads.
        service.capture_run(context, task_type='demo', policy_version='m1-v1',
                            tool_sequence=[{'name':'value_portfolio'}], outcome='success',
                            feedback='demo run; redaction applied at capture')
        retrieved = service.retrieve_memories(context, 'fixture import',
                                              query_time=now.isoformat())
        # M6 paper simulation: one offline walkthrough on a separate ledger with an
        # operator-ingested synthetic observation. Real holdings are compared before/after;
        # only counts, a cash string and a boolean are printed (no ids, symbols or bodies).
        store.create_user('demo-operator')
        operator = auth.authenticate(auth.provision('demo-operator', role='operator'))
        OperatorService(service).ingest_observation(operator, Observation(
            'fixture:demo', Decimal('10'), now, 'synthetic-fixture', 'price', fetched_at=now))
        holdings_before = service.holdings(context, portfolio)
        paper_account = service.create_paper_account(context, portfolio, source='synthetic-fixture',
                                                     mode='paper')
        service.paper_deposit(context, paper_account, '1000', source='synthetic-fixture',
                              idempotency_key='demo-deposit', as_of=now, mode='paper')
        service.submit_paper_order(context, paper_account, 'fixture:demo', 'buy', '1', as_of=now,
                                   idempotency_key='demo-buy', mode='paper')
        paper_value = service.value_paper_account(context, paper_account, as_of=now)
        paper_positions = service.paper_positions(context, paper_account)
        print(json.dumps({'mode':'read_only','holdings':service.holdings(context,portfolio),
                          'paper_fill_count':sum(1 for e in service.paper_ledger(context,paper_account)
                                                 if e['entry_type'] in ('buy','sell')),
                          'paper_cash_text':paper_positions['cash'],
                          'paper_valuation_status':paper_value['status'],
                          'paper_real_holdings_unchanged':service.holdings(context,portfolio)==holdings_before,
                          'memory_count':len(service.memories(context)),
                          'trace_count':len(service.traces(context)),
                          'jobs_claimed':len(claimed),'jobs_done':jobs_done,
                          'outbox_count':outbox_count,'notifications_delivered':delivered,
                          'chat_evidence_count':len(chat_out['evidence']),
                          'chat_proposed_action_count':len(chat_out['proposed_actions']),
                          'run_record_count':len(service.run_records(context)),
                          'retrieved_memory_count':len(retrieved)},indent=2))
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
            from .m4_evaluation import evaluate as evaluate_m4
            from .m5a_evaluation import evaluate as evaluate_m5a
            from .m5b_evaluation import evaluate as evaluate_m5b
            from .m6_evaluation import evaluate as evaluate_m6
            from .web_security_evaluation import evaluate as evaluate_web_security
            reports = [evaluate_safety(), evaluate_import(), evaluate_m2(),
                       evaluate_m3(), evaluate_m4(), evaluate_m5a(), evaluate_m5b(), evaluate_m6(),
                       evaluate_web_security()]
        else:
            from .valuation_evaluation import evaluate
            reports = [evaluate()]
        print(json.dumps(reports if len(reports)>1 else reports[0],indent=2))
        if any(report['passed'] != report['total'] for report in reports):
            raise SystemExit(1)

if __name__ == '__main__':
    main()
