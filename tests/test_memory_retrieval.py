"""M5a scoped keyword-retrieval tests (red-first; SUBSTRATE ONLY, no learning claim).

Retrieval applies OWNERSHIP and STATUS/EXPIRY filters BEFORE ranking, in SQL, so excluded
rows never reach the ranker. Ranking is a deterministic keyword score (no embeddings this
slice). Returning a stored note is NOT behavioral learning. Fixtures are obviously synthetic.
"""
import unittest
from dataclasses import replace
from portfolio_agent.core import Store, FixtureValidator


QUERY_TIME = '2026-06-01T00:00:00+00:00'


class ScopedRetrievalTests(unittest.TestCase):
    def setUp(self):
        from portfolio_agent.auth import Authenticator
        from portfolio_agent.services import PortfolioService, OperatorService
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob', 'admin'):
            self.s.create_user(user)
        self.auth = Authenticator(self.s)
        self.tokens = {u: self.auth.provision(u, role='operator' if u == 'admin' else 'user')
                       for u in ('alice', 'bob', 'admin')}
        self.contexts = {u: self.auth.authenticate(t) for u, t in self.tokens.items()}
        self.service = PortfolioService(self.s, self.auth, FixtureValidator({}))
        self.operator = OperatorService(self.service)

    def _add(self, user, body, **kw):
        return self.s.add_private_memory(user, kw.pop('kind', 'semantic'), body, 'fixture', **kw)

    def test_authorization_applied_before_rank(self):
        # Bob's row would out-score alice's on the query, but must never appear for alice.
        self._add('bob', 'mutual fund nav mutual fund nav mutual fund')
        self._add('alice', 'mutual fund note')
        rows = self.service.retrieve_memories(self.contexts['alice'], 'mutual fund nav',
                                              query_time=QUERY_TIME)
        bodies = [r['body'] for r in rows]
        self.assertIn('mutual fund note', bodies)
        self.assertNotIn('mutual fund nav mutual fund nav mutual fund', bodies)

    def test_forged_cross_user_context_denied(self):
        self._add('bob', 'bob secret fund')
        forged = replace(self.contexts['alice'], user_id='bob')
        with self.assertRaises(PermissionError):
            self.service.retrieve_memories(forged, 'fund', query_time=QUERY_TIME)

    def test_status_and_expiry_filtered_before_rank(self):
        self._add('alice', 'active fund keep')
        expired = self._add('alice', 'expired fund drop', expires_at='2020-01-01T00:00:00+00:00')
        deprecated = self._add('alice', 'deprecated fund drop')
        self.s.db.execute("UPDATE memories SET status='deprecated' WHERE id=?", (deprecated,))
        self.s.db.commit()
        bodies = [r['body'] for r in self.service.retrieve_memories(
            self.contexts['alice'], 'fund', query_time=QUERY_TIME)]
        self.assertIn('active fund keep', bodies)
        self.assertNotIn('expired fund drop', bodies)
        self.assertNotIn('deprecated fund drop', bodies)

    def test_unreviewed_procedure_not_returned_as_shared(self):
        # An un-reviewed procedural row owned by bob must never surface for alice. The DB
        # forbids a public+procedural+unreviewed row, so it can only exist as bob-private.
        self._add('bob', 'procedure nav handling', kind='procedural')
        rows = self.service.retrieve_memories(self.contexts['alice'], 'procedure nav',
                                              query_time=QUERY_TIME)
        self.assertEqual(rows, [])

    def test_deterministic_keyword_and_tiebreak_ordering(self):
        # Higher keyword overlap ranks first.
        self._add('alice', 'alpha beta gamma')
        self._add('alice', 'alpha only')
        ordered = [r['body'] for r in self.service.retrieve_memories(
            self.contexts['alice'], 'alpha beta gamma', query_time=QUERY_TIME)]
        self.assertEqual(ordered[0], 'alpha beta gamma')
        # Two equal-keyword rows: tie-break newer created_at, then stable id.
        first = self._add('alice', 'tie keyword')
        second = self._add('alice', 'tie keyword')
        self.s.db.execute("UPDATE memories SET created_at='2026-01-01T00:00:00+00:00' WHERE id=?", (first,))
        self.s.db.execute("UPDATE memories SET created_at='2026-02-01T00:00:00+00:00' WHERE id=?", (second,))
        self.s.db.commit()
        tie = [r['id'] for r in self.service.retrieve_memories(
            self.contexts['alice'], 'tie keyword', query_time=QUERY_TIME)
            if r['body'] == 'tie keyword']
        self.assertEqual(tie[:2], [second, first])

    def test_returned_rows_carry_score_and_matched_tokens(self):
        self._add('alice', 'dividend growth fund')
        rows = self.service.retrieve_memories(self.contexts['alice'], 'dividend fund',
                                              query_time=QUERY_TIME)
        self.assertTrue(rows)
        self.assertIn('score', rows[0])
        self.assertIn('matched_tokens', rows[0])
        self.assertEqual(set(rows[0]['matched_tokens']), {'dividend', 'fund'})

    def test_public_semantic_facts_returned_alongside_private(self):
        self.operator.ingest_public_fact(self.contexts['admin'], 'public index fund fact', 'fixture')
        self._add('alice', 'private index note')
        bodies = [r['body'] for r in self.service.retrieve_memories(
            self.contexts['alice'], 'index', query_time=QUERY_TIME)]
        self.assertIn('public index fund fact', bodies)
        self.assertIn('private index note', bodies)

    def test_retrieval_is_deterministic(self):
        for body in ('one fund', 'two fund', 'three fund'):
            self._add('alice', body)
        first = self.service.retrieve_memories(self.contexts['alice'], 'fund', query_time=QUERY_TIME)
        second = self.service.retrieve_memories(self.contexts['alice'], 'fund', query_time=QUERY_TIME)
        self.assertEqual([r['id'] for r in first], [r['id'] for r in second])


if __name__ == '__main__':
    unittest.main()
