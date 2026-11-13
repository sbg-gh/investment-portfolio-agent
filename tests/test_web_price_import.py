"""POST /api/operator/prices/import: operator-only, CSRF/Origin, fixed codes, no echoed content.
Synthetic fixtures only (dates are in the past relative to the real clock)."""
import unittest
from portfolio_agent.core import ValidatedSecurity
from web_client import make_env

URL = '/api/operator/prices/import'
HEAD = 'symbol,date,close\n'
DAY = '2020-01-02'
ROW = f'SYNA,{DAY},1.5\n'


class PriceImportWebTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(full=True)
        self.c = {}
        for name in ('alice', 'admin'):
            self.c[name] = self.env.client()
            self.c[name].login(self.env.tokens[name])
        for sid, symbol in (('s1', 'SYNA'), ('s2', 'SYNB')):
            self.env.store.register(ValidatedSecurity(sid, symbol, 'stock', 'US', 'synthetic-test'))

    def count(self, table):
        return self.env.store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def post(self, text, *, dry_run=False, source='web-src', who='admin', **kw):
        return self.c[who].request('POST', URL, json={'source': source, 'csv_text': text,
                                                      'dry_run': dry_run}, **kw)

    def test_unauthenticated_is_401(self):
        self.assertEqual(self.env.client().request('POST', URL, json={}).status, 401)

    def test_non_operator_is_403_and_nothing_is_written(self):
        reply = self.post(HEAD + ROW, who='alice')
        self.assertEqual((reply.status, reply.body), (403, b'{"error":"forbidden"}'))
        self.assertEqual(self.count('market_observations'), 0)

    def test_csrf_and_origin_are_required(self):
        for kw in ({'csrf': False}, {'origin': False}):
            self.assertEqual(self.post(HEAD + ROW, **kw).status, 403)
        self.assertEqual(self.count('market_observations'), 0)

    def test_malformed_json_and_oversized_body(self):
        self.assertEqual(self.c['admin'].request('POST', URL, body=b'{nope').status, 400)
        big = self.c['admin'].request('POST', URL, body=b'{"x":"' + b'a' * 65537 + b'"}')
        self.assertEqual(big.status, 413)

    def test_wrong_types_and_missing_fields(self):
        c = self.c['admin']
        bodies = [{'csv_text': HEAD + ROW, 'dry_run': True},
                  {'source': 'x', 'dry_run': True},
                  {'source': 'x', 'csv_text': HEAD + ROW},
                  {'source': 'x', 'csv_text': HEAD + ROW, 'dry_run': 'true'},
                  {'source': 'x', 'csv_text': HEAD + ROW, 'dry_run': 1},
                  {'source': 5, 'csv_text': HEAD + ROW, 'dry_run': True},
                  {'source': 'x', 'csv_text': 7, 'dry_run': True}]
        for body in bodies:
            reply = c.request('POST', URL, json=body)
            self.assertEqual(reply.status, 422, body)
        self.assertEqual(self.count('market_observations'), 0)

    def test_dry_run_shape_and_database_unchanged(self):
        reply = self.post(HEAD + ROW + f'NOPE,{DAY},2\n', dry_run=True)
        self.assertEqual(reply.status, 200)
        self.assertEqual(reply.json(), {
            'dry_run': True, 'source': 'web-src', 'total_rows': 2,
            'counts': {'imported': 1, 'unknown_symbol': 1}, 'ignored_columns': [],
            'rows': [{'line': 2, 'symbol': 'SYNA', 'outcome': 'imported'},
                     {'line': 3, 'symbol': 'NOPE', 'outcome': 'unknown_symbol'}]})
        self.assertEqual(self.count('market_observations'), 0)
        self.assertEqual(self.count('securities'), 2)

    def test_import_correct_and_unchanged_flow_and_sources_list(self):
        reply = self.post(HEAD + ROW)
        self.assertEqual((reply.status, reply.json()['counts']), (200, {'imported': 1}))
        self.assertEqual(self.c['alice'].request('GET', '/api/sources').json(), ['web-src'])
        self.assertEqual(self.post(HEAD + ROW).json()['counts'], {'unchanged': 1})
        reply = self.post(HEAD + f'SYNA,{DAY},2.5\n')
        self.assertEqual(reply.json()['counts'], {'corrected': 1})
        rows = self.env.store.db.execute('SELECT value,source,basis FROM market_observations ORDER BY rowid').fetchall()
        self.assertEqual(rows[0], ('1.5', 'web-src', 'price'))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(isinstance(v, str) for v, _, _ in rows))

    def test_file_level_errors_return_only_a_fixed_code(self):
        cases = {'invalid_header': 'a,b\n1,2\n', 'empty_file': HEAD, 'invalid_csv': HEAD + '"x,2020-01-02,1\n'}
        for code, text in cases.items():
            reply = self.post(text)
            self.assertEqual((reply.status, reply.json()), (422, {'error': code}))
        reply = self.post(HEAD + ROW, source='bad source')
        self.assertEqual((reply.status, reply.json()), (422, {'error': 'invalid_source'}))
        reply = self.post(HEAD + ROW * 4000)
        self.assertEqual((reply.status, reply.json()), (413, {'error': 'body_too_large'}))
        self.assertEqual(self.count('market_observations'), 0)

    def test_no_cell_text_in_responses_or_logs(self):
        text = (f'symbol,date,close,volume\nSECRET symbol!,{DAY},1,SECRET-V\n'
                f'SYNA,{DAY},SECRET-CLOSE,v\nSYNA,SECRET-DATE,1,v\nSYNB,{DAY},7.7,v\n')
        reply = self.post(text, dry_run=True)
        self.assertEqual(reply.status, 200)
        self.assertEqual(reply.json()['rows'][0], {'line': 2, 'symbol': None, 'outcome': 'invalid_format'})
        self.assertNotIn('SECRET', reply.body.decode())
        self.assertNotIn('7.7', reply.body.decode())
        self.post(text)
        joined = '\n'.join(str(line) for line in self.env.logs)
        for needle in ('SECRET', 'SYNA', 'SYNB', 'web-src', '7.7'):
            self.assertNotIn(needle, joined)

    def test_body_user_id_is_ignored(self):
        reply = self.c['alice'].request('POST', URL, json={'user_id': 'admin', 'source': 'x',
                                                           'csv_text': HEAD + ROW, 'dry_run': True})
        self.assertEqual(reply.status, 403)


if __name__ == '__main__':
    unittest.main()
