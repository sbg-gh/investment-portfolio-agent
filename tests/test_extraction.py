"""M5b deterministic, owner-scoped, offline candidate extraction (red-first).

Extraction turns observed run records into UNREVIEWED procedural candidates. It makes no
learning claim: a candidate is un-shareable and inactive until review, held-out evaluation
and approval.
"""
import json
import sqlite3
import unittest

from portfolio_agent.leakage import HeldOutLeakageError, make_canary
from portfolio_agent import procedures
from tests.m5b_fixtures import ETF, MF, make_world, seed_runs, seed_training


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.s, self.auth, self.service, self.operator, self.ctx = make_world()
        self.addCleanup(self.s.close)

    def test_extracts_one_unreviewed_private_procedural_candidate(self):
        seed_training(self.s, 'alice')
        ids = procedures.extract_candidates(self.s, 'alice')
        self.assertEqual(len(ids), 1)
        row = self.s.db.execute(
            "SELECT user_id,scope,kind,status,review_status,version,payload,confidence,"
            "evidence_ids,applicable_conditions,body FROM memories WHERE id=?", (ids[0],)).fetchone()
        owner, scope, kind, status, review, version, payload, confidence, evidence, cond, body = row
        self.assertEqual((owner, scope, kind, status, review, version),
                         ('alice', 'private', 'procedural', 'active', 'unreviewed', 1))
        self.assertEqual(json.loads(payload), {'schema': 'procedure-v1',
                                               'when': {'security_kind': 'mutual_fund'},
                                               'prefer_tool': 'dated_nav', 'over_tool': 'intraday_quote'})
        self.assertEqual(confidence, '1')
        self.assertEqual(len(json.loads(evidence)), 5)
        self.assertEqual(json.loads(cond)['security_kind'], 'mutual_fund')
        self.assertEqual(body, 'For mutual_fund valuation questions prefer the dated_nav tool '
                               'over the intraday_quote tool.')
        self.assertEqual(procedures.current_state(self.s, ids[0]), 'candidate')

    def test_confidence_reflects_contradicting_runs(self):
        seed_runs(self.s, 'alice', MF, 'intraday_quote', 'stale_data', 2)
        seed_runs(self.s, 'alice', MF, 'dated_nav', 'success', 3)
        seed_runs(self.s, 'alice', MF, 'dated_nav', 'error', 1)
        (mem_id,) = procedures.extract_candidates(self.s, 'alice')
        self.assertEqual(self.s.db.execute("SELECT confidence FROM memories WHERE id=?",
                                           (mem_id,)).fetchone()[0], '0.75')

    def test_extraction_is_deterministic_and_idempotent(self):
        seed_training(self.s, 'alice')
        first = procedures.extract_candidates(self.s, 'alice')
        again = procedures.extract_candidates(self.s, 'alice')
        self.assertEqual(len(first), 1)
        self.assertEqual(again, [])
        self.assertEqual(self.s.db.execute(
            "SELECT COUNT(*) FROM memories WHERE kind='procedural'").fetchone()[0], 1)
        self.assertEqual(self.s.db.execute(
            "SELECT COUNT(*) FROM procedure_events WHERE event='extracted'").fetchone()[0], 1)
        # A second identical world yields an identical payload and body (determinism).
        other, *_ = make_world()
        self.addCleanup(other.close)
        seed_training(other, 'alice')
        (other_id,) = procedures.extract_candidates(other, 'alice')
        self.assertEqual(
            other.db.execute("SELECT payload,body,confidence FROM memories WHERE id=?", (other_id,)).fetchone(),
            self.s.db.execute("SELECT payload,body,confidence FROM memories WHERE id=?", (first[0],)).fetchone())

    def test_owner_scoping_other_users_runs_never_used(self):
        seed_training(self.s, 'bob')
        self.assertEqual(procedures.extract_candidates(self.s, 'alice'), [])
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        (bob_id,) = procedures.extract_candidates(self.s, 'bob')
        self.assertEqual(self.s.db.execute("SELECT user_id FROM memories WHERE id=?", (bob_id,)).fetchone()[0], 'bob')

    def test_mixed_owner_runs_do_not_combine(self):
        seed_runs(self.s, 'alice', MF, 'intraday_quote', 'stale_data', 3)
        seed_runs(self.s, 'bob', MF, 'dated_nav', 'success', 5)
        self.assertEqual(procedures.extract_candidates(self.s, 'alice'), [])
        self.assertEqual(procedures.extract_candidates(self.s, 'bob'), [])

    def test_unknown_user_denied(self):
        with self.assertRaises(PermissionError):
            procedures.extract_candidates(self.s, 'mallory')

    def test_control_task_family_yields_no_candidate(self):
        seed_runs(self.s, 'alice', ETF, 'intraday_quote', 'success', 6)
        self.assertEqual(procedures.extract_candidates(self.s, 'alice'), [])

    def test_feedback_text_never_reaches_body_or_payload(self):
        secret = 'ZZQ rebalance alice@example.invalid'
        seed_training(self.s, 'alice', feedback=secret)
        (mem_id,) = procedures.extract_candidates(self.s, 'alice')
        blob = json.dumps(self.s.db.execute(
            "SELECT body,payload,evidence_ids,applicable_conditions,source,confidence FROM memories WHERE id=?",
            (mem_id,)).fetchone())
        events = json.dumps(self.s.db.execute("SELECT reason,evaluation FROM procedure_events").fetchall())
        for needle in ('ZZQ', 'rebalance', 'alice@example', 'example.invalid'):
            self.assertNotIn(needle, blob)
            self.assertNotIn(needle, events)

    def test_non_identifier_task_kind_or_tool_is_skipped(self):
        seed_runs(self.s, 'alice', 'valuation:Bad Kind!', 'intraday_quote', 'stale_data', 2)
        seed_runs(self.s, 'alice', 'valuation:Bad Kind!', 'dated_nav', 'success', 3)
        seed_runs(self.s, 'alice', MF, 'Tool With Spaces', 'stale_data', 2)
        seed_runs(self.s, 'alice', MF, 'dated_nav', 'success', 3)
        self.assertEqual(procedures.extract_candidates(self.s, 'alice'), [])

    def test_multi_tool_sequences_are_not_used(self):
        for _ in range(3):
            self.s.record_run('alice', request_id='r', task_type=MF, policy_version='m1-v1',
                              tool_sequence=['list_holdings', 'dated_nav'], outcome='success')
        seed_runs(self.s, 'alice', MF, 'intraday_quote', 'stale_data', 2)
        self.assertEqual(procedures.extract_candidates(self.s, 'alice'), [])

    def test_candidate_is_not_shareable(self):
        seed_training(self.s, 'alice')
        (mem_id,) = procedures.extract_candidates(self.s, 'alice')
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.db.execute(
                "INSERT INTO memories(id,user_id,scope,kind,body,source,review_status) "
                "VALUES ('pub',NULL,'public','procedural','b','s','unreviewed')")
        self.s.db.rollback()
        self.assertEqual(self.s.db.execute("SELECT review_status FROM memories WHERE id=?",
                                           (mem_id,)).fetchone()[0], 'unreviewed')

    def test_held_out_run_records_are_refused_and_never_extracted(self):
        with self.assertRaises(HeldOutLeakageError):
            self.s.record_run('alice', request_id='r', task_type='heldout:' + MF, policy_version='m1-v1',
                              tool_sequence=['dated_nav'], outcome='success')
        with self.assertRaises(HeldOutLeakageError):
            self.s.record_run('alice', request_id='r', task_type=MF, policy_version='m1-v1',
                              tool_sequence=['dated_nav'], outcome='success', feedback=make_canary('c1'))
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM run_records").fetchone()[0], 0)
        self.assertEqual(procedures.extract_candidates(self.s, 'alice'), [])

    def test_service_extraction_uses_auth_context_and_payload_free_trace(self):
        seed_training(self.s, 'alice')
        seed_training(self.s, 'bob', stale=1, nav=1)
        ids = self.service.extract_procedure_candidates(self.ctx['alice'])
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.s.db.execute("SELECT user_id FROM memories WHERE id=?", (ids[0],)).fetchone()[0], 'alice')
        trace = self.s.db.execute(
            "SELECT operation,outcome FROM execution_traces ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual(trace, ('extract_procedure_candidates', 'success'))


if __name__ == '__main__':
    unittest.main()
