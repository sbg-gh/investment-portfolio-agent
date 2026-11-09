"""M5a owner-scoped run-capture tests (red-first; SUBSTRATE ONLY, no learning claim).

Capturing a run writes ONE owner-scoped `run_records` row through the authorized path, with
secrets redacted and the tool sequence reduced to NAMES only. It must NOT regress M1's
payload-free execution trace: `execution_traces` keeps exactly the M1 column set and still
records the metadata-only trace for the captured operation. Identity comes only from the
AuthContext; no caller-supplied owner id is ever trusted.
"""
import json
import unittest
from portfolio_agent.core import Store, FixtureValidator

# The frozen M1 execution_traces column set — capture must never widen it.
M1_TRACE_COLUMNS = {'id', 'user_id', 'request_id', 'operation', 'outcome',
                    'policy_version', 'duration_ms', 'created_at'}


class RunCaptureTests(unittest.TestCase):
    def setUp(self):
        from portfolio_agent.auth import Authenticator
        from portfolio_agent.services import PortfolioService
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob'):
            self.s.create_user(user)
        self.auth = Authenticator(self.s)
        self.contexts = {u: self.auth.authenticate(self.auth.provision(u)) for u in ('alice', 'bob')}
        self.service = PortfolioService(self.s, self.auth, FixtureValidator({}))

    def _capture(self, context, **overrides):
        kwargs = dict(task_type='chat', policy_version='m1-v1',
                      model_version='fixture-model',
                      tool_sequence=[{'name': 'list_holdings', 'args': {'portfolio_id': 'p-secret'}}],
                      outcome='success',
                      feedback='password=hunter2 the answer was useful',
                      cost='0.01', latency_ms=12.5)
        kwargs.update(overrides)
        return self.service.capture_run(context, **kwargs)

    def test_capture_writes_one_owner_scoped_row_with_tool_names_only(self):
        self._capture(self.contexts['alice'])
        rows = self.s.db.execute(
            "SELECT user_id,task_type,policy_version,model_version,tool_sequence,outcome "
            "FROM run_records").fetchall()
        self.assertEqual(len(rows), 1)
        user_id, task_type, policy_version, model_version, tool_sequence, outcome = rows[0]
        self.assertEqual(user_id, 'alice')
        self.assertEqual(task_type, 'chat')
        self.assertEqual(policy_version, 'm1-v1')
        self.assertEqual(model_version, 'fixture-model')
        self.assertEqual(json.loads(tool_sequence), ['list_holdings'])
        self.assertEqual(outcome, 'success')

    def test_capture_redacts_secret_and_drops_tool_args(self):
        self._capture(self.contexts['alice'])
        row = self.s.db.execute("SELECT feedback,tool_sequence FROM run_records").fetchone()
        blob = json.dumps(row)
        self.assertNotIn('hunter2', blob)
        self.assertNotIn('p-secret', blob)

    def test_tool_sequence_never_contains_args_or_results(self):
        self._capture(self.contexts['alice'], tool_sequence=[
            {'name': 'value_portfolio', 'args': {'portfolio_id': 'p-secret', 'token': 'tok_secret'}},
            {'name': 'list_holdings', 'result': {'AAPL': '10'}}])
        stored = self.s.db.execute("SELECT tool_sequence FROM run_records").fetchone()[0]
        self.assertEqual(json.loads(stored), ['value_portfolio', 'list_holdings'])
        self.assertNotIn('tok_secret', stored)
        self.assertNotIn('AAPL', stored)

    def test_cross_user_run_records_read_denied(self):
        self._capture(self.contexts['alice'])
        # Bob only ever sees his own (empty) run records.
        self.assertEqual(self.service.run_records(self.contexts['bob']), [])
        # The Store accessor is owner-scoped; alice sees exactly one.
        self.assertEqual(len(self.s.run_records('alice')), 1)

    def test_execution_traces_column_set_is_unchanged(self):
        columns = {row[1] for row in self.s.db.execute('PRAGMA table_info(execution_traces)').fetchall()}
        self.assertEqual(columns, M1_TRACE_COLUMNS)

    def test_metadata_only_trace_written_and_correlated_by_request_id(self):
        self._capture(self.contexts['alice'])
        # A payload-free capture_run trace is written for the operation.
        traces = self.service.traces(self.contexts['alice'])
        operations = [t[2] for t in traces]
        self.assertIn('capture_run', operations)
        # The run record shares the request_id with the trace correlation token.
        run_request = self.s.db.execute("SELECT request_id FROM run_records").fetchone()[0]
        trace_request_ids = [t[1] for t in traces]
        self.assertIn(run_request, trace_request_ids)
        # The trace carries no feedback/tool payload.
        self.assertNotIn('hunter2', json.dumps(traces))

    def test_capture_does_not_accept_caller_supplied_owner(self):
        # capture_run derives identity from context; there is no owner/user_id parameter.
        import inspect
        sig = inspect.signature(self.service.capture_run)
        self.assertNotIn('user_id', sig.parameters)
        self.assertNotIn('owner', sig.parameters)
        # A forged context (relabelled user) is denied, nothing captured.
        from dataclasses import replace
        forged = replace(self.contexts['bob'], user_id='alice')
        with self.assertRaises(PermissionError):
            self._capture(forged)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM run_records').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
