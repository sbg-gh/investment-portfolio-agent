"""M5b procedure promotion lifecycle, versioning, rollback and publish (red-first).

Legal transitions succeed, illegal ones raise ValueError and write nothing. A single run, user
feedback or a non-significant held-out result can never promote a procedure. Lineage is
append-only and survives rollback. Publishing a generic procedure is operator-only and gated
by a privacy review against the owner's private corpus.
"""
import json
import unittest

from portfolio_agent import procedures
from portfolio_agent.core import FixtureValidator, ValidatedSecurity
from portfolio_agent.m5b_stats import load_prereg
from tests.m5b_fixtures import MF, make_world, seed_runs, seed_training

IMPROVE = {'approved_vs_retrieval_only': (24, 0), 'approved_vs_no_memory': (24, 0)}
SMALL = {'approved_vs_retrieval_only': (5, 0), 'approved_vs_no_memory': (5, 0)}
REGRESS = {'approved_vs_retrieval_only': (0, 10), 'approved_vs_no_memory': (0, 10)}


def report(contrasts=IMPROVE, *, gate_ok=True, n_target=32, **extra):
    data = {'contrasts': dict(contrasts), 'n_target': n_target, 'gate_ok': gate_ok,
            'report_sha256': 'a' * 64}
    data.update(extra)
    return data


LEGAL_FROM = {'review': {'candidate'}, 'evaluate': {'reviewed', 'evaluated'},
              'approve': {'evaluated'}, 'activate': {'approved'},
              'deprecate': {'approved', 'active'}, 'rollback': {'active'}}
OPERATIONS = {
    'review': lambda s, m: procedures.review_candidate(s, 'alice', m),
    'evaluate': lambda s, m: procedures.record_evaluation(s, 'alice', m, report()),
    'approve': lambda s, m: procedures.approve(s, 'alice', m),
    'activate': lambda s, m: procedures.activate(s, 'alice', m),
    'deprecate': lambda s, m: procedures.deprecate(s, 'alice', m, reason='retire'),
    'rollback': lambda s, m: procedures.rollback(s, 'alice', m, reason='undo'),
}


def advance(store, memory_id, target, user='alice'):
    """Drive a candidate to `target` through the public lifecycle functions."""
    if target == 'candidate':
        return
    procedures.review_candidate(store, user, memory_id)
    if target == 'reviewed':
        return
    procedures.record_evaluation(store, user, memory_id, report())
    if target == 'evaluated':
        return
    procedures.approve(store, user, memory_id)
    if target == 'approved':
        return
    procedures.activate(store, user, memory_id)
    if target == 'active':
        return
    if target == 'deprecated':
        procedures.deprecate(store, user, memory_id, reason='retire')
    elif target == 'rolled_back':
        procedures.rollback(store, user, memory_id, reason='undo')


class LifecycleBase(unittest.TestCase):
    def setUp(self):
        self.s, self.auth, self.service, self.operator, self.ctx = make_world()
        self.addCleanup(self.s.close)

    def candidate(self, store=None, **training):
        store = store or self.s
        seed_training(store, 'alice', **training)
        (memory_id,) = procedures.extract_candidates(store, 'alice')
        return memory_id

    def row(self, memory_id, store=None):
        return (store or self.s).db.execute(
            "SELECT status,review_status,version,supersedes FROM memories WHERE id=?", (memory_id,)).fetchone()

    def two_versions(self):
        v1 = self.candidate()
        advance(self.s, v1, 'active')
        v2 = procedures.propose_version(self.s, 'alice', v1)
        advance(self.s, v2, 'active')
        return v1, v2

    def events(self, memory_id=None):
        sql = "SELECT COUNT(*) FROM procedure_events"
        args = ()
        if memory_id:
            sql += " WHERE memory_id=?"
            args = (memory_id,)
        return self.s.db.execute(sql, args).fetchone()[0]


class TransitionTests(LifecycleBase):
    def test_full_legal_path_updates_state_and_memory_gates(self):
        m = self.candidate()
        self.assertEqual(self.row(m)[:2], ('active', 'unreviewed'))
        expected = [('review', 'reviewed', ('active', 'unreviewed')),
                    ('evaluate', 'evaluated', ('active', 'unreviewed')),
                    ('approve', 'approved', ('active', 'approved')),
                    ('activate', 'active', ('active', 'approved'))]
        for op, state, gates in expected:
            OPERATIONS[op](self.s, m)
            self.assertEqual(procedures.current_state(self.s, m), state)
            self.assertEqual(self.row(m)[:2], gates)
        OPERATIONS['deprecate'](self.s, m)
        self.assertEqual(procedures.current_state(self.s, m), 'deprecated')
        self.assertEqual(self.row(m)[0], 'deprecated')

    def test_rollback_from_active(self):
        m = self.candidate()
        advance(self.s, m, 'active')
        OPERATIONS['rollback'](self.s, m)
        self.assertEqual(procedures.current_state(self.s, m), 'rolled_back')
        self.assertEqual(self.row(m)[0], 'deprecated')

    def test_deprecate_from_approved(self):
        m = self.candidate()
        advance(self.s, m, 'approved')
        OPERATIONS['deprecate'](self.s, m)
        self.assertEqual(procedures.current_state(self.s, m), 'deprecated')

    def test_re_evaluation_is_legal(self):
        m = self.candidate()
        advance(self.s, m, 'evaluated')
        OPERATIONS['evaluate'](self.s, m)
        self.assertEqual(procedures.current_state(self.s, m), 'evaluated')

    def test_every_illegal_transition_raises_and_writes_nothing(self):
        for target in ('candidate', 'reviewed', 'evaluated', 'approved', 'active',
                       'deprecated', 'rolled_back'):
            for op, legal in LEGAL_FROM.items():
                if target in legal:
                    continue
                with self.subTest(state=target, op=op):
                    store, *_ = make_world()
                    self.addCleanup(store.close)
                    m = self.candidate(store)
                    advance(store, m, target)
                    before_events = store.db.execute("SELECT COUNT(*) FROM procedure_events").fetchone()[0]
                    before_row = self.row(m, store)
                    with self.assertRaises(ValueError):
                        OPERATIONS[op](store, m)
                    self.assertEqual(store.db.execute("SELECT COUNT(*) FROM procedure_events").fetchone()[0],
                                     before_events)
                    self.assertEqual(self.row(m, store), before_row)
                    self.assertEqual(procedures.current_state(store, m), target)

    def test_rejected_is_terminal(self):
        m = self.candidate(nav=2)
        result = procedures.review_candidate(self.s, 'alice', m)
        self.assertFalse(result['passed'])
        self.assertEqual(procedures.current_state(self.s, m), 'rejected')
        self.assertEqual(self.row(m)[0], 'deprecated')
        for op in LEGAL_FROM:
            with self.subTest(op=op), self.assertRaises(ValueError):
                OPERATIONS[op](self.s, m)

    def test_transitions_table_is_explicit(self):
        self.assertIn(('candidate', 'reviewed'), procedures.TRANSITIONS)
        self.assertNotIn(('candidate', 'approved'), procedures.TRANSITIONS)
        self.assertNotIn(('candidate', 'active'), procedures.TRANSITIONS)
        self.assertNotIn(('rolled_back', 'active'), procedures.TRANSITIONS)
        self.assertNotIn(('rejected', 'reviewed'), procedures.TRANSITIONS)

    def test_procedure_without_lifecycle_events_is_fail_closed(self):
        mem = self.s.add_private_memory('alice', 'procedural', 'hand written', 'fixture')
        with self.assertRaises(ValueError):
            procedures.review_candidate(self.s, 'alice', mem)
        self.assertEqual(procedures.active_procedures(self.s, 'alice'), [])

    def test_deleted_memory_cannot_be_promoted(self):
        m = self.candidate()
        self.s.delete_memory('alice', m, reason='user request')
        with self.assertRaises(ValueError):
            procedures.review_candidate(self.s, 'alice', m)


class ReviewTests(LifecycleBase):
    def test_single_supporting_run_never_passes(self):
        m = self.candidate(stale=1, nav=1)
        result = procedures.review_candidate(self.s, 'alice', m)
        self.assertEqual((result['passed'], result['supporting'], result['contradicting']), (False, 1, 0))
        self.assertEqual(procedures.current_state(self.s, m), 'rejected')

    def test_feedback_is_not_evidence(self):
        m = self.candidate(nav=2, feedback='excellent, definitely correct, promote this')
        result = procedures.review_candidate(self.s, 'alice', m)
        self.assertFalse(result['passed'])
        self.assertEqual(procedures.current_state(self.s, m), 'rejected')

    def test_needs_three_supporting_and_zero_contradicting(self):
        m = self.candidate(nav=3)
        result = procedures.review_candidate(self.s, 'alice', m)
        self.assertTrue(result['passed'])
        self.assertEqual(procedures.current_state(self.s, m), 'reviewed')
        store, *_ = make_world()
        self.addCleanup(store.close)
        seed_training(store, 'alice', nav=4)
        (c,) = procedures.extract_candidates(store, 'alice')
        seed_runs(store, 'alice', MF, 'dated_nav', 'error', 1)
        result = procedures.review_candidate(store, 'alice', c)
        self.assertEqual((result['passed'], result['contradicting']), (False, 1))
        self.assertEqual(procedures.current_state(store, c), 'rejected')

    def test_review_recomputes_from_owner_runs_not_stored_evidence(self):
        m = self.candidate()
        # Another user's matching runs must not count for alice's review.
        seed_runs(self.s, 'bob', MF, 'dated_nav', 'error', 5)
        self.assertTrue(procedures.review_candidate(self.s, 'alice', m)['passed'])

    def test_reviewing_alone_never_reaches_active(self):
        m = self.candidate()
        procedures.review_candidate(self.s, 'alice', m)
        self.assertEqual(procedures.active_procedures(self.s, 'alice'), [])
        with self.assertRaises(ValueError):
            procedures.approve(self.s, 'alice', m)
        with self.assertRaises(ValueError):
            procedures.activate(self.s, 'alice', m)


class EvaluationTests(LifecycleBase):
    def reviewed(self):
        m = self.candidate()
        advance(self.s, m, 'reviewed')
        return m

    def test_verdict_is_recomputed_and_forged_verdict_ignored(self):
        m = self.reviewed()
        digest = procedures.record_evaluation(
            self.s, 'alice', m, report(SMALL, verdict='significant_improvement'))
        self.assertEqual(digest['verdict'], 'inconclusive')
        stored = procedures.lineage(self.s, 'alice', m)[-1]['evaluation']
        self.assertEqual(stored['verdict'], 'inconclusive')
        self.assertEqual(stored['prereg_sha256'], load_prereg()[1])
        self.assertEqual(stored['report_sha256'], 'a' * 64)
        with self.assertRaises(ValueError):
            procedures.approve(self.s, 'alice', m)
        self.assertEqual(procedures.current_state(self.s, m), 'evaluated')

    def test_regression_verdict_cannot_be_approved(self):
        m = self.reviewed()
        digest = procedures.record_evaluation(self.s, 'alice', m, report(REGRESS))
        self.assertEqual(digest['verdict'], 'significant_regression')
        with self.assertRaises(ValueError):
            procedures.approve(self.s, 'alice', m)

    def test_too_small_dataset_cannot_be_approved(self):
        m = self.reviewed()
        digest = procedures.record_evaluation(self.s, 'alice', m, report({'approved_vs_retrieval_only': (9, 0), 'approved_vs_no_memory': (9, 0)}, n_target=10))
        self.assertEqual((digest['verdict'], digest['reason']), ('inconclusive', 'too_small'))
        with self.assertRaises(ValueError):
            procedures.approve(self.s, 'alice', m)

    def test_failed_gate_blocks_approval_even_with_significant_improvement(self):
        m = self.reviewed()
        digest = procedures.record_evaluation(self.s, 'alice', m, report(gate_ok=False))
        self.assertEqual(digest['verdict'], 'significant_improvement')
        self.assertFalse(digest['gate_ok'])
        with self.assertRaises(ValueError):
            procedures.approve(self.s, 'alice', m)

    def test_latest_evaluation_governs_approval(self):
        m = self.reviewed()
        procedures.record_evaluation(self.s, 'alice', m, report())
        procedures.record_evaluation(self.s, 'alice', m, report(SMALL))
        with self.assertRaises(ValueError):
            procedures.approve(self.s, 'alice', m)
        procedures.record_evaluation(self.s, 'alice', m, report())
        procedures.approve(self.s, 'alice', m)
        self.assertEqual(self.row(m)[1], 'approved')

    def test_malformed_report_rejected_and_nothing_written(self):
        m = self.reviewed()
        before = self.events()
        for bad in (report(gate_ok='yes'), {'n_target': 32, 'gate_ok': True},
                    report({'approved_vs_no_memory': (24, 0)}), report(n_target='32'),
                    report(report_sha256='not-hex')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                procedures.record_evaluation(self.s, 'alice', m, bad)
        self.assertEqual(self.events(), before)

    def test_evaluation_digest_holds_numbers_not_free_text(self):
        m = self.reviewed()
        procedures.record_evaluation(self.s, 'alice', m, report(note='alice@example.invalid secret'))
        raw = self.s.db.execute("SELECT evaluation FROM procedure_events WHERE event='evaluated'").fetchone()[0]
        self.assertNotIn('example.invalid', raw)
        self.assertEqual(set(json.loads(raw)),
                         {'verdict', 'reason', 'contrasts', 'gate_ok', 'n_target',
                          'prereg_sha256', 'report_sha256'})


class AuthorizationTests(LifecycleBase):
    def test_cross_user_operations_denied_without_change(self):
        m = self.candidate()
        advance(self.s, m, 'evaluated')
        before_events = self.events()
        before_row = self.row(m)
        calls = [
            lambda: procedures.review_candidate(self.s, 'bob', m),
            lambda: procedures.record_evaluation(self.s, 'bob', m, report()),
            lambda: procedures.approve(self.s, 'bob', m),
            lambda: procedures.activate(self.s, 'bob', m),
            lambda: procedures.deprecate(self.s, 'bob', m, reason='x'),
            lambda: procedures.rollback(self.s, 'bob', m, reason='x'),
            lambda: procedures.check_regression(self.s, 'bob', m, report(REGRESS)),
            lambda: procedures.lineage(self.s, 'bob', m),
            lambda: procedures.propose_version(self.s, 'bob', m),
        ]
        for call in calls:
            with self.assertRaises(PermissionError):
                call()
        self.assertEqual(self.events(), before_events)
        self.assertEqual(self.row(m), before_row)

    def test_unknown_memory_denied(self):
        with self.assertRaises(PermissionError):
            procedures.approve(self.s, 'alice', 'no-such-memory')

    def test_non_procedural_or_public_memory_is_not_a_procedure(self):
        mem = self.s.add_private_memory('alice', 'semantic', 'note', 'fixture')
        with self.assertRaises(PermissionError):
            procedures.review_candidate(self.s, 'alice', mem)

    def test_active_procedures_are_owner_scoped(self):
        m = self.candidate()
        advance(self.s, m, 'active')
        self.assertEqual([p['id'] for p in procedures.active_procedures(self.s, 'alice')], [m])
        self.assertEqual(procedures.active_procedures(self.s, 'bob'), [])


class ActiveProcedureTests(LifecycleBase):
    def test_active_procedure_view_is_structured(self):
        m = self.candidate()
        advance(self.s, m, 'active')
        (proc,) = procedures.active_procedures(self.s, 'alice')
        self.assertEqual(proc['payload'], {'schema': 'procedure-v1',
                                           'when': {'security_kind': 'mutual_fund'},
                                           'prefer_tool': 'dated_nav', 'over_tool': 'intraday_quote'})
        self.assertEqual((proc['id'], proc['version']), (m, 1))

    def test_unreviewed_procedure_never_listed_even_if_lifecycle_row_forged(self):
        m = self.candidate()
        self.s.db.execute(
            "INSERT INTO procedure_events(id,memory_id,owner_id,actor_id,event,from_state,to_state,trigger) "
            "VALUES ('forged',?, 'alice','alice','activated','approved','active','manual')", (m,))
        self.s.db.commit()
        self.assertEqual(self.row(m)[1], 'unreviewed')
        self.assertEqual(procedures.active_procedures(self.s, 'alice'), [])

    def test_only_active_state_is_listed(self):
        for target in ('candidate', 'reviewed', 'evaluated', 'approved', 'deprecated', 'rolled_back'):
            with self.subTest(state=target):
                store, *_ = make_world()
                self.addCleanup(store.close)
                m = self.candidate(store)
                advance(store, m, target)
                self.assertEqual(procedures.active_procedures(store, 'alice'), [])


class VersioningAndRollbackTests(LifecycleBase):
    def test_propose_version_creates_unreviewed_next_version_without_touching_v1(self):
        v1 = self.candidate()
        advance(self.s, v1, 'active')
        v2 = procedures.propose_version(self.s, 'alice', v1)
        self.assertEqual(self.row(v2), ('active', 'unreviewed', 2, v1))
        self.assertEqual(procedures.current_state(self.s, v2), 'candidate')
        self.assertEqual(procedures.current_state(self.s, v1), 'active')
        self.assertEqual(self.row(v1)[0], 'active')
        self.assertEqual([p['id'] for p in procedures.active_procedures(self.s, 'alice')], [v1])

    def test_propose_version_requires_active_predecessor(self):
        v1 = self.candidate()
        with self.assertRaises(ValueError):
            procedures.propose_version(self.s, 'alice', v1)

    def test_activating_v2_supersedes_v1(self):
        v1, v2 = self.two_versions()
        self.assertEqual(self.row(v1)[0], 'superseded')
        self.assertEqual(procedures.current_state(self.s, v1), 'deprecated')
        self.assertEqual(self.row(v2)[0], 'active')
        self.assertEqual([p['id'] for p in procedures.active_procedures(self.s, 'alice')], [v2])
        self.assertEqual(procedures.lineage(self.s, 'alice', v1)[-1]['event'], 'superseded')

    def test_rollback_restores_prior_version_and_retains_lineage(self):
        v1, v2 = self.two_versions()
        v1_events = len(procedures.lineage(self.s, 'alice', v1))
        v2_events = len(procedures.lineage(self.s, 'alice', v2))
        procedures.rollback(self.s, 'alice', v2, reason='behaviour worse')
        self.assertEqual(procedures.current_state(self.s, v2), 'rolled_back')
        self.assertEqual(self.row(v2)[0], 'deprecated')
        self.assertEqual(procedures.current_state(self.s, v1), 'active')
        self.assertEqual(self.row(v1)[0], 'active')
        self.assertEqual([p['id'] for p in procedures.active_procedures(self.s, 'alice')], [v1])
        lineage_v1 = procedures.lineage(self.s, 'alice', v1)
        lineage_v2 = procedures.lineage(self.s, 'alice', v2)
        self.assertEqual(len(lineage_v1), v1_events + 1)
        self.assertEqual(len(lineage_v2), v2_events + 1)
        self.assertEqual(lineage_v1[-1]['event'], 'restored')
        self.assertEqual(lineage_v1[-1]['trigger'], 'manual')
        self.assertEqual(lineage_v2[-1]['event'], 'rolled_back')
        # Evaluation lineage (numeric digests) is retained after rollback.
        evals = [e['evaluation'] for e in lineage_v2 if e['event'] == 'evaluated']
        self.assertEqual(len(evals), 1)
        self.assertEqual(evals[0]['verdict'], 'significant_improvement')

    def test_rollback_without_predecessor_leaves_nothing_active(self):
        v1 = self.candidate()
        advance(self.s, v1, 'active')
        procedures.rollback(self.s, 'alice', v1, reason='undo')
        self.assertEqual(procedures.active_procedures(self.s, 'alice'), [])

    def test_manual_deprecation_of_v2_does_not_restore_v1(self):
        v1, v2 = self.two_versions()
        procedures.deprecate(self.s, 'alice', v2, reason='retire')
        self.assertEqual(procedures.current_state(self.s, v1), 'deprecated')
        self.assertEqual(procedures.active_procedures(self.s, 'alice'), [])

    def test_lineage_is_append_only(self):
        v1, v2 = self.two_versions()
        procedures.rollback(self.s, 'alice', v2, reason='undo')
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.db.execute("DELETE FROM procedure_events WHERE memory_id=?", (v2,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.db.execute("UPDATE procedure_events SET reason='x' WHERE memory_id=?", (v2,))

    def test_lineage_records_actor_trigger_and_bounded_redacted_reason(self):
        m = self.candidate()
        advance(self.s, m, 'active')
        procedures.rollback(self.s, 'alice', m, reason='password=hunter2 ' + 'x' * 1000)
        entry = procedures.lineage(self.s, 'alice', m)[-1]
        self.assertEqual((entry['event'], entry['actor_id'], entry['trigger']),
                         ('rolled_back', 'alice', 'manual'))
        self.assertNotIn('hunter2', entry['reason'])
        self.assertLessEqual(len(entry['reason']), 280)

    def test_reason_required_for_rollback_and_deprecate(self):
        m = self.candidate()
        advance(self.s, m, 'active')
        for reason in ('', '   ', None):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    procedures.rollback(self.s, 'alice', m, reason=reason)
                with self.assertRaises(ValueError):
                    procedures.deprecate(self.s, 'alice', m, reason=reason)
        self.assertEqual(procedures.current_state(self.s, m), 'active')

    def test_held_out_canary_in_reason_is_refused(self):
        from portfolio_agent.leakage import HeldOutLeakageError, make_canary
        m = self.candidate()
        advance(self.s, m, 'active')
        with self.assertRaises(HeldOutLeakageError):
            procedures.rollback(self.s, 'alice', m, reason=make_canary('case-1'))
        self.assertEqual(procedures.current_state(self.s, m), 'active')


class RegressionTests(LifecycleBase):
    def test_significant_regression_rolls_back_automatically(self):
        v1, v2 = self.two_versions()
        outcome = procedures.check_regression(self.s, 'alice', v2, report(REGRESS))
        self.assertTrue(outcome['rolled_back'])
        self.assertEqual(outcome['verdict'], 'significant_regression')
        last = procedures.lineage(self.s, 'alice', v2)[-1]
        self.assertEqual((last['event'], last['trigger']), ('rolled_back', 'regression'))
        self.assertEqual(last['evaluation']['verdict'], 'significant_regression')
        restored = procedures.lineage(self.s, 'alice', v1)[-1]
        self.assertEqual((restored['event'], restored['trigger']), ('restored', 'regression'))
        self.assertEqual([p['id'] for p in procedures.active_procedures(self.s, 'alice')], [v1])

    def test_failed_gate_triggers_rollback(self):
        v1 = self.candidate()
        advance(self.s, v1, 'active')
        outcome = procedures.check_regression(self.s, 'alice', v1, report(gate_ok=False))
        self.assertTrue(outcome['rolled_back'])
        self.assertEqual(procedures.current_state(self.s, v1), 'rolled_back')

    def test_inconclusive_and_improvement_do_nothing(self):
        m = self.candidate()
        advance(self.s, m, 'active')
        before = self.events()
        for rep in (report(SMALL), report(IMPROVE)):
            outcome = procedures.check_regression(self.s, 'alice', m, rep)
            self.assertFalse(outcome['rolled_back'])
        self.assertEqual(self.events(), before)
        self.assertEqual(procedures.current_state(self.s, m), 'active')

    def test_regression_check_on_inactive_procedure_does_nothing(self):
        m = self.candidate()
        advance(self.s, m, 'approved')
        outcome = procedures.check_regression(self.s, 'alice', m, report(REGRESS))
        self.assertFalse(outcome['rolled_back'])
        self.assertEqual(procedures.current_state(self.s, m), 'approved')

    def test_forged_verdict_in_regression_report_is_ignored(self):
        m = self.candidate()
        advance(self.s, m, 'active')
        outcome = procedures.check_regression(self.s, 'alice', m, report(IMPROVE, verdict='significant_regression'))
        self.assertFalse(outcome['rolled_back'])


class PublishTests(LifecycleBase):
    def plant_private_context(self, kind_symbol='ZZQ'):
        validator = FixtureValidator({kind_symbol: ValidatedSecurity('sec-zzq', kind_symbol, 'mutual_fund',
                                                                      'US', 'fixture')})
        self.portfolio = self.s.create_portfolio('alice', 'zzq-retirement')
        self.s.import_holding('alice', self.portfolio, kind_symbol, '3', validator)
        self.s.add_private_memory('alice', 'semantic', 'my retirement plan favors quarterly review', 'fixture')

    def active_candidate(self, **training):
        m = self.candidate(**training)
        advance(self.s, m, 'active')
        return m

    def test_non_operator_denied(self):
        m = self.active_candidate()
        with self.assertRaises(PermissionError):
            self.operator.publish_generic_procedure(self.ctx['bob'], m)
        with self.assertRaises(PermissionError):
            self.operator.publish_generic_procedure(self.ctx['alice'], m)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM memories WHERE scope='public'").fetchone()[0], 0)

    def test_inactive_procedure_cannot_be_published(self):
        for target in ('candidate', 'reviewed', 'evaluated', 'approved', 'deprecated', 'rolled_back'):
            with self.subTest(state=target):
                store, _auth, _service, operator, ctx = make_world()
                self.addCleanup(store.close)
                m = self.candidate(store)
                advance(store, m, target)
                with self.assertRaises(ValueError):
                    operator.publish_generic_procedure(ctx['operator'], m)
                self.assertEqual(store.db.execute(
                    "SELECT COUNT(*) FROM memories WHERE scope='public'").fetchone()[0], 0)

    def test_publish_creates_clean_reviewed_public_row_visible_to_other_users(self):
        self.plant_private_context()
        m = self.active_candidate(feedback='rebalance quarterly please')
        public_id = self.operator.publish_generic_procedure(self.ctx['operator'], m)
        row = self.s.db.execute(
            "SELECT user_id,scope,kind,review_status,status,payload,body,source FROM memories WHERE id=?",
            (public_id,)).fetchone()
        self.assertEqual(row[:5], (None, 'public', 'procedural', 'approved', 'active'))
        self.assertEqual(json.loads(row[5]), {'schema': 'procedure-v1',
                                              'when': {'security_kind': 'mutual_fund'},
                                              'prefer_tool': 'dated_nav', 'over_tool': 'intraday_quote'})
        full = json.dumps(self.s.db.execute("SELECT * FROM memories WHERE id=?", (public_id,)).fetchone()).lower()
        for marker in ('alice', 'zzq', 'sec-zzq', self.portfolio, 'rebalance', 'retirement', 'quarterly', 'bob'):
            self.assertNotIn(marker.lower(), full)
        hits = self.service.retrieve_memories(self.ctx['bob'], 'mutual_fund dated_nav valuation',
                                              query_time='2026-06-01T00:00:00+00:00', kind='procedural')
        self.assertIn(public_id, [h['id'] for h in hits])
        event = procedures.lineage(self.s, 'alice', m)[-1]
        self.assertEqual((event['event'], event['actor_id'], event['trigger']),
                         ('published', 'operator', 'operator'))
        self.assertEqual(procedures.current_state(self.s, m), 'active')

    def test_publish_is_idempotent(self):
        m = self.active_candidate()
        first = self.operator.publish_generic_procedure(self.ctx['operator'], m)
        again = self.operator.publish_generic_procedure(self.ctx['operator'], m)
        self.assertEqual(first, again)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM memories WHERE scope='public'").fetchone()[0], 1)

    def test_privacy_review_blocks_planted_symbol_in_generic_text(self):
        self.plant_private_context()
        seed_runs(self.s, 'alice', 'valuation:zzq_fund', 'intraday_quote', 'stale_data', 2)
        seed_runs(self.s, 'alice', 'valuation:zzq_fund', 'dated_nav', 'success', 3)
        ids = procedures.extract_candidates(self.s, 'alice')
        (m,) = ids
        advance(self.s, m, 'active')
        with self.assertRaises(ValueError):
            self.operator.publish_generic_procedure(self.ctx['operator'], m)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM memories WHERE scope='public'").fetchone()[0], 0)
        self.assertEqual(procedures.lineage(self.s, 'alice', m)[-1]['event'], 'activated')

    def test_privacy_review_blocks_user_id_and_feedback_phrase(self):
        seed_runs(self.s, 'alice', 'valuation:alice', 'intraday_quote', 'stale_data', 2)
        seed_runs(self.s, 'alice', 'valuation:alice', 'dated_nav', 'success', 3)
        (m,) = procedures.extract_candidates(self.s, 'alice')
        advance(self.s, m, 'active')
        with self.assertRaises(ValueError):
            self.operator.publish_generic_procedure(self.ctx['operator'], m)
        store, *_ = make_world()
        self.addCleanup(store.close)
        seed_runs(store, 'alice', 'valuation:quirky_fund', 'intraday_quote', 'stale_data', 2)
        seed_runs(store, 'alice', 'valuation:quirky_fund', 'dated_nav', 'success', 3, feedback='quirky fund')
        (m2,) = procedures.extract_candidates(store, 'alice')
        advance(store, m2, 'active')
        self.assertTrue(procedures.privacy_review(store, 'alice', procedures.generic_text(store, m2)))

    def test_privacy_review_unit_detects_each_marker_class(self):
        self.plant_private_context()
        seed_runs(self.s, 'alice', MF, 'dated_nav', 'success', 1, feedback='tell no one about nonsuch')
        self.s.add_private_memory('alice', 'semantic', 'prefers lunar dividends only', 'fixture')
        clean = 'For mutual_fund valuation questions prefer the dated_nav tool over the intraday_quote tool.'
        self.assertEqual(procedures.privacy_review(self.s, 'alice', clean), [])
        planted = {
            'symbol': 'prefer ZZQ tool',
            'security id': 'uses sec-zzq here',
            'portfolio id': 'in portfolio ' + self.portfolio,
            'user id': 'owner alice wants this',
            'feedback phrase': 'note: tell no one about nonsuch',
            'preference text': 'because prefers lunar dividends only',
        }
        for name, text in planted.items():
            with self.subTest(marker=name):
                self.assertTrue(procedures.privacy_review(self.s, 'alice', text), name)

    def test_generic_content_is_built_from_whitelisted_fields_only(self):
        m = self.active_candidate()
        self.s.db.execute("UPDATE memories SET body='SECRET free text alice@example.invalid' WHERE id=?", (m,))
        self.s.db.commit()
        public_id = self.operator.publish_generic_procedure(self.ctx['operator'], m)
        body = self.s.db.execute("SELECT body FROM memories WHERE id=?", (public_id,)).fetchone()[0]
        self.assertEqual(body, 'For mutual_fund valuation questions prefer the dated_nav tool '
                               'over the intraday_quote tool.')

    def test_tampered_payload_with_non_identifier_field_is_refused(self):
        m = self.active_candidate()
        bad = json.dumps({'schema': 'procedure-v1', 'when': {'security_kind': 'mutual_fund'},
                          'prefer_tool': 'dated_nav; alice@example.invalid', 'over_tool': 'intraday_quote'})
        self.s.db.execute("UPDATE memories SET payload=? WHERE id=?", (bad, m))
        self.s.db.commit()
        with self.assertRaises(ValueError):
            self.operator.publish_generic_procedure(self.ctx['operator'], m)

    def test_unreviewed_procedural_row_still_cannot_be_shared(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.db.execute("INSERT INTO memories(id,user_id,scope,kind,body,source,review_status) "
                              "VALUES ('x',NULL,'public','procedural','b','s','unreviewed')")
        self.s.db.rollback()


class ServiceBoundaryTests(LifecycleBase):
    def test_service_methods_trace_payload_free_and_enforce_ownership(self):
        seed_training(self.s, 'alice')
        alice, bob = self.ctx['alice'], self.ctx['bob']
        columns_before = [r[1] for r in self.s.db.execute('PRAGMA table_info(execution_traces)')]
        (m,) = self.service.extract_procedure_candidates(alice)
        self.service.review_procedure(alice, m)
        self.service.record_procedure_evaluation(alice, m, report())
        self.service.approve_procedure(alice, m)
        self.service.activate_procedure(alice, m)
        self.assertEqual([p['id'] for p in self.service.active_procedures(alice)], [m])
        self.assertGreaterEqual(len(self.service.procedure_lineage(alice, m)), 5)
        v2 = self.service.propose_procedure_version(alice, m)
        self.assertEqual(self.row(v2)[2], 2)
        self.service.check_procedure_regression(alice, m, report(IMPROVE))
        self.service.review_procedure(alice, v2)
        self.service.record_procedure_evaluation(alice, v2, report())
        self.service.approve_procedure(alice, v2)
        self.service.deprecate_procedure(alice, v2, reason='not needed')
        self.service.rollback_procedure(alice, m, reason='undo')
        operations = [t[2] for t in self.service.traces(alice)]
        for name in ('extract_procedure_candidates', 'review_procedure', 'record_procedure_evaluation',
                     'approve_procedure', 'activate_procedure', 'active_procedures', 'procedure_lineage',
                     'propose_procedure_version', 'check_procedure_regression', 'deprecate_procedure',
                     'rollback_procedure'):
            self.assertIn(name, operations)
        self.assertEqual([r[1] for r in self.s.db.execute('PRAGMA table_info(execution_traces)')],
                         columns_before)
        blob = json.dumps(self.s.db.execute("SELECT * FROM execution_traces").fetchall())
        for needle in ('dated_nav', 'intraday_quote', 'mutual_fund', m, 'significant'):
            self.assertNotIn(needle, blob)
        # Denied calls still leave an outcome trace and change nothing.
        before = self.events()
        with self.assertRaises(PermissionError):
            self.service.approve_procedure(bob, m)
        self.assertEqual(self.events(), before)
        self.assertEqual(self.service.traces(bob)[-1][2:4], ('approve_procedure', 'denied'))
        with self.assertRaises(ValueError):
            self.service.activate_procedure(alice, m)
        self.assertEqual(self.service.traces(alice)[-1][2:4], ('activate_procedure', 'invalid'))


if __name__ == '__main__':
    unittest.main()
