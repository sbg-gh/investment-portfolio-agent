"""Deterministic M5a memory-substrate regression fixtures. NOT held-out learning evidence.

Each scenario drives an isolated in-memory Store through the authorized services, observing
ONE M5a substrate behavior: scoped retrieval excludes cross-user rows, status/expiry filters
apply before ranking, an un-reviewed procedure is never shared, keyword ranking is
deterministic, run capture redacts a planted secret, the execution trace stays payload-free,
and deletion lineage is retained.

SUBSTRATE ONLY. This is a SCOPE/CAPTURE/RETRIEVAL correctness gate, kept DISTINCT from any
held-out learning evaluation. It makes NO learning/adaptation/improvement claim — there is no
`learning_improvement` key. Fixtures are obviously synthetic and fully offline (no network,
no live model).
"""
import hashlib
from importlib.resources import files
import json

from .auth import Authenticator
from .core import FixtureValidator, Store
from .services import OperatorService, PortfolioService, POLICY_VERSION

SCENARIOS = {
    'scoped_retrieval_excludes_cross_user',
    'status_expiry_filtered_before_rank',
    'unreviewed_procedure_not_shared',
    'deterministic_keyword_ranking',
    'run_capture_redacts_secret',
    'trace_stays_payload_free',
    'deletion_lineage_retained',
}

QUERY_TIME = '2026-06-01T00:00:00+00:00'

# The frozen M1 execution_traces column set — capture must never widen it.
TRACE_COLUMNS = {'id', 'user_id', 'request_id', 'operation', 'outcome',
                 'policy_version', 'duration_ms', 'created_at'}


def load_cases():
    return json.loads(files('portfolio_agent').joinpath('m5a_cases.json').read_text())


def _build():
    """Isolated store with synthetic owners + an operator and the services over it."""
    store = Store()
    for user in ('alice', 'bob', 'operator'):
        store.create_user(user)
    auth = Authenticator(store)
    ctx = {u: auth.authenticate(
        auth.provision(u, role='operator' if u == 'operator' else 'user'))
        for u in ('alice', 'bob', 'operator')}
    app = PortfolioService(store, auth, FixtureValidator({}))
    ops = OperatorService(app)
    return store, app, ops, ctx


def _scenario(name):
    if name == 'scoped_retrieval_excludes_cross_user':
        store, app, _ops, ctx = _build()
        try:
            store.add_private_memory('bob', 'semantic', 'synthetic fund nav fund nav fund', 'fixture')
            store.add_private_memory('alice', 'semantic', 'synthetic fund note', 'fixture')
            rows = app.retrieve_memories(ctx['alice'], 'fund nav', query_time=QUERY_TIME)
            bodies = {r['body'] for r in rows}
            return ('owner_only' if 'synthetic fund note' in bodies
                    and 'synthetic fund nav fund nav fund' not in bodies else 'leaked')
        finally:
            store.close()
    if name == 'status_expiry_filtered_before_rank':
        store, app, _ops, ctx = _build()
        try:
            store.add_private_memory('alice', 'semantic', 'synthetic active fund', 'fixture')
            store.add_private_memory('alice', 'semantic', 'synthetic expired fund', 'fixture',
                                     expires_at='2020-01-01T00:00:00+00:00')
            dep = store.add_private_memory('alice', 'semantic', 'synthetic deprecated fund', 'fixture')
            store.db.execute("UPDATE memories SET status='deprecated' WHERE id=?", (dep,))
            store.db.commit()
            bodies = {r['body'] for r in app.retrieve_memories(
                ctx['alice'], 'fund', query_time=QUERY_TIME)}
            return ('filtered' if bodies == {'synthetic active fund'} else 'unfiltered')
        finally:
            store.close()
    if name == 'unreviewed_procedure_not_shared':
        store, app, _ops, ctx = _build()
        try:
            # An un-reviewed procedural row owned by bob must never surface for alice.
            store.add_private_memory('bob', 'procedural', 'synthetic procedure nav', 'fixture')
            rows = app.retrieve_memories(ctx['alice'], 'procedure nav', query_time=QUERY_TIME)
            return 'not_shared' if rows == [] else 'shared'
        finally:
            store.close()
    if name == 'deterministic_keyword_ranking':
        store, app, _ops, ctx = _build()
        try:
            store.add_private_memory('alice', 'semantic', 'synthetic alpha beta gamma', 'fixture')
            store.add_private_memory('alice', 'semantic', 'synthetic alpha only', 'fixture')
            ordered = [r['body'] for r in app.retrieve_memories(
                ctx['alice'], 'alpha beta gamma', query_time=QUERY_TIME)]
            return ('ranked' if ordered and ordered[0] == 'synthetic alpha beta gamma'
                    else 'misranked')
        finally:
            store.close()
    if name == 'run_capture_redacts_secret':
        store, app, _ops, ctx = _build()
        try:
            app.capture_run(ctx['alice'], task_type='chat', policy_version='m1-v1',
                            model_version='fixture-model',
                            tool_sequence=[{'name': 'list_holdings',
                                            'args': {'portfolio_id': 'p-secret'}}],
                            outcome='success', feedback='password=hunter2 synthetic feedback')
            row = store.db.execute("SELECT feedback,tool_sequence FROM run_records").fetchone()
            blob = json.dumps(row)
            return ('redacted' if 'hunter2' not in blob and 'p-secret' not in blob
                    and json.loads(row[1]) == ['list_holdings'] else 'leaked')
        finally:
            store.close()
    if name == 'trace_stays_payload_free':
        store, app, _ops, ctx = _build()
        try:
            app.capture_run(ctx['alice'], task_type='chat', policy_version='m1-v1',
                            tool_sequence=[{'name': 'list_holdings'}], outcome='success',
                            feedback='password=hunter2 synthetic feedback')
            columns = {row[1] for row in store.db.execute(
                'PRAGMA table_info(execution_traces)').fetchall()}
            traces = app.traces(ctx['alice'])
            payload_free = 'hunter2' not in json.dumps(traces)
            correlated = any(t[2] == 'capture_run' for t in traces)
            return ('payload_free' if columns == TRACE_COLUMNS and payload_free and correlated
                    else 'regressed')
        finally:
            store.close()
    if name == 'deletion_lineage_retained':
        store, app, _ops, ctx = _build()
        try:
            store.add_private_memory('alice', 'semantic', 'synthetic to delete', 'fixture')
            mem_id = store.db.execute(
                "SELECT id FROM memories WHERE body='synthetic to delete'").fetchone()[0]
            app.delete_memory(ctx['alice'], mem_id, reason='user requested')
            status = store.db.execute("SELECT status FROM memories WHERE id=?", (mem_id,)).fetchone()[0]
            lineage = store.db.execute(
                "SELECT previous_status,reason FROM memory_deletions WHERE memory_id=?",
                (mem_id,)).fetchone()
            excluded = app.retrieve_memories(ctx['alice'], 'to delete', query_time=QUERY_TIME) == []
            return ('lineage_retained' if status == 'deleted'
                    and lineage == ('active', 'user requested') and excluded else 'lost')
        finally:
            store.close()
    return 'unknown'


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
    return {'suite': 'm5a-memory-baseline-v1', 'policy_version': POLICY_VERSION,
            'dataset_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            'total': len(results), 'passed': sum(r['passed'] for r in results), 'results': results}
