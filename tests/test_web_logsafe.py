"""Access and error log lines carry only method, route template, status, duration, request id."""
import random
import string
import unittest

from portfolio_agent.web import logsafe

RID = '123e4567-e89b-42d3-a456-426614174000'


class LogSafeTests(unittest.TestCase):
    def test_access_line_shape(self):
        line = logsafe.access_line('POST', '/api/session', 401, 12.7, RID)
        self.assertEqual(line, f'POST /api/session 401 12ms {RID}')

    def test_unknown_methods_and_templates_and_ids_are_neutralised(self):
        line = logsafe.access_line('BREW\r\nX: y', None, 404, 1, 'bad id\n')
        self.assertEqual(line, 'OTHER - 404 1ms -')

    def test_hostile_values_cannot_inject_lines_or_leak(self):
        planted = ['AAPL-secret-symbol', 'tok_abc123', '1234.5678', 'alice@example', 'sid=deadbeef']
        rng = random.Random(7)
        for _ in range(200):
            junk = ''.join(rng.choice(string.printable) for _ in range(30)) + rng.choice(planted)
            line = logsafe.access_line(junk, junk, rng.choice([200, 404, 500]), rng.random() * 1000, junk)
            self.assertNotIn('\n', line)
            self.assertNotIn('\r', line)
            for secret in planted:
                self.assertNotIn(secret, line)

    def test_error_line_has_class_name_and_request_id_only(self):
        try:
            raise RuntimeError('db password=hunter2 for alice')
        except RuntimeError as exc:
            line = logsafe.error_line(exc, RID)
        self.assertEqual(line, f'ERROR RuntimeError {RID}')
        self.assertNotIn('hunter2', line)

    def test_error_line_neutralises_hostile_class_name_and_id(self):
        class Evil(Exception):
            pass
        Evil.__name__ = 'X\nINJECTED'
        self.assertEqual(logsafe.error_line(Evil('x'), 'a\nb'), 'ERROR Exception -')


if __name__ == '__main__':
    unittest.main()
