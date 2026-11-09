"""M5a memory-substrate contract tests (SUBSTRATE ONLY; no learning/adaptation claim).

Written red-first against the v6 memory model. Covers the richer unified-table record
contract, the EXTENDED scope CHECK (un-reviewed procedures are un-shareable at the DB layer),
preserved M1 scope/quarantine behavior, and deletion-lineage/supersede semantics. None of
this establishes behavioral learning; retrieving a prior note is not adaptation.
"""
import sqlite3
import unittest
from portfolio_agent.core import Store, FixtureValidator, ValidatedSecurity


class MemoryContractTests(unittest.TestCase):
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
        self.v = FixtureValidator({'DEMO': ValidatedSecurity('s0', 'DEMO', 'etf', 'US', 'fixture')})
        self.service = PortfolioService(self.s, self.auth, self.v)
        self.operator = OperatorService(self.service)
        self.p = self.service.create_portfolio(self.contexts['alice'], 'private name')

    def test_add_private_memory_accepts_m1_and_richer_forms(self):
        # M1 3-arg form still works and defaults the new fields.
        self.s.add_private_memory('alice', 'episodic', 'simple note', 'fixture')
        row = self.s.db.execute(
            "SELECT status,version,review_status,portfolio_id,payload FROM memories "
            "WHERE user_id='alice' AND body='simple note'").fetchone()
        self.assertEqual(row, ('active', 1, 'unreviewed', None, None))
        # Richer form carries the new fields and is owner-scoped on portfolio_id.
        self.s.add_private_memory('alice', 'semantic', 'rich note', 'fixture',
                                  portfolio_id=self.p, payload='{"k":"v"}',
                                  evidence_ids='["e1"]', expires_at='2999-01-01T00:00:00+00:00',
                                  confidence='0.9', applicable_conditions='mutual_fund',
                                  source_run_id='run-1')
        rich = self.s.db.execute(
            "SELECT portfolio_id,payload,evidence_ids,expires_at,confidence,"
            "applicable_conditions,source_run_id,status,version,review_status "
            "FROM memories WHERE body='rich note'").fetchone()
        self.assertEqual(rich, (self.p, '{"k":"v"}', '["e1"]', '2999-01-01T00:00:00+00:00',
                                '0.9', 'mutual_fund', 'run-1', 'active', 1, 'unreviewed'))

    def test_richer_memory_portfolio_ownership_is_authorized(self):
        # A memory cannot be scoped to another user's portfolio.
        bob_p = self.service.create_portfolio(self.contexts['bob'], 'bob private')
        with self.assertRaises(PermissionError):
            self.s.add_private_memory('alice', 'semantic', 'cross', 'fixture', portfolio_id=bob_p)

    def test_extended_scope_check_blocks_unreviewed_shared_procedure(self):
        # public + procedural + unreviewed is rejected at the DB layer.
        with self.assertRaises(sqlite3.IntegrityError):
            with self.s.db:
                self.s.db.execute(
                    "INSERT INTO memories(id,user_id,scope,kind,body,source,review_status) "
                    "VALUES ('pp',NULL,'public','procedural','proc','source','unreviewed')")
        # public + procedural + approved is allowed.
        with self.s.db:
            self.s.db.execute(
                "INSERT INTO memories(id,user_id,scope,kind,body,source,review_status) "
                "VALUES ('pa',NULL,'public','procedural','proc','source','approved')")
        self.assertEqual(
            self.s.db.execute("SELECT review_status FROM memories WHERE id='pa'").fetchone(),
            ('approved',))
        # public + episodic is still rejected.
        with self.assertRaises(sqlite3.IntegrityError):
            with self.s.db:
                self.s.db.execute(
                    "INSERT INTO memories(id,user_id,scope,kind,body,source) "
                    "VALUES ('pe',NULL,'public','episodic','ep','source')")

    def test_m1_scope_mixture_regression_still_holds(self):
        for owner, scope, kind in ((None, 'private', 'episodic'),
                                   ('alice', 'public', 'semantic')):
            with self.subTest(owner=owner, scope=scope, kind=kind):
                with self.assertRaises(sqlite3.IntegrityError):
                    with self.s.db:
                        self.s.db.execute(
                            'INSERT INTO memories(id,user_id,scope,kind,body,source) VALUES (?,?,?,?,?,?)',
                            ('bad', owner, scope, kind, 'secret', 'source'))

    def test_null_owner_private_memory_still_rejected(self):
        for owner in (None, '', 'missing'):
            with self.subTest(owner=owner):
                with self.assertRaises(PermissionError):
                    self.s.add_private_memory(owner, 'episodic', 'private secret', 'fixture')

    def test_public_semantic_ingestion_still_inserts(self):
        self.operator.ingest_public_fact(self.contexts['admin'], 'public fact', 'fixture')
        self.assertEqual(self.s.retrieve_memory('bob'), [('semantic', 'public fact', 'fixture')])


class DeletionLineageTests(unittest.TestCase):
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

    def _one_memory(self, user='alice', kind='semantic', body='fact'):
        self.s.add_private_memory(user, kind, body, 'fixture')
        return self.s.db.execute(
            "SELECT id FROM memories WHERE user_id=? AND body=?", (user, body)).fetchone()[0]

    def test_delete_memory_sets_status_and_writes_lineage(self):
        mem_id = self._one_memory()
        self.s.delete_memory('alice', mem_id, reason='user requested')
        status = self.s.db.execute("SELECT status FROM memories WHERE id=?", (mem_id,)).fetchone()[0]
        self.assertEqual(status, 'deleted')
        lineage = self.s.db.execute(
            "SELECT memory_id,previous_status,reason,scope FROM memory_deletions "
            "WHERE memory_id=?", (mem_id,)).fetchone()
        self.assertEqual(lineage, (mem_id, 'active', 'user requested', 'private'))
        # The body is not resurrected into retrieval.
        rows = self.s.retrieve_scoped_memories('alice', 'fact',
                                               query_time='2026-01-01T00:00:00+00:00')
        self.assertEqual(rows, [])

    def test_supersede_bumps_version_and_records_lineage(self):
        mem_id = self._one_memory(body='old fact')
        new_id = self.s.supersede_memory('alice', mem_id, reason='correction',
                                         kind='semantic', body='new fact', source='fixture')
        old = self.s.db.execute("SELECT status FROM memories WHERE id=?", (mem_id,)).fetchone()[0]
        new = self.s.db.execute(
            "SELECT status,version,supersedes FROM memories WHERE id=?", (new_id,)).fetchone()
        self.assertEqual(old, 'superseded')
        self.assertEqual(new, ('active', 2, mem_id))
        lineage = self.s.db.execute(
            "SELECT previous_status,reason FROM memory_deletions WHERE memory_id=?",
            (mem_id,)).fetchone()
        self.assertEqual(lineage, ('active', 'correction'))

    def test_superseded_and_deleted_rows_excluded_from_retrieval(self):
        deleted = self._one_memory(body='to delete')
        superseded = self._one_memory(body='to supersede')
        self.s.delete_memory('alice', deleted, reason='r')
        self.s.supersede_memory('alice', superseded, reason='r',
                                kind='semantic', body='to supersede', source='fixture')
        bodies = {r['body'] for r in self.s.retrieve_scoped_memories(
            'alice', 'to', query_time='2026-01-01T00:00:00+00:00')}
        self.assertNotIn('to delete', bodies)
        # Only the active replacement survives, not the superseded original row.
        active = self.s.db.execute(
            "SELECT COUNT(*) FROM memories WHERE body='to supersede' AND status='active'").fetchone()[0]
        self.assertEqual(active, 1)

    def test_cannot_delete_or_supersede_another_users_memory(self):
        mem_id = self._one_memory(user='bob', body='bob private')
        with self.assertRaises(PermissionError):
            self.s.delete_memory('alice', mem_id, reason='attack')
        with self.assertRaises(PermissionError):
            self.s.supersede_memory('alice', mem_id, reason='attack',
                                    kind='semantic', body='x', source='fixture')
        # Bob's memory is untouched.
        self.assertEqual(
            self.s.db.execute("SELECT status FROM memories WHERE id=?", (mem_id,)).fetchone()[0],
            'active')


if __name__ == '__main__':
    unittest.main()
