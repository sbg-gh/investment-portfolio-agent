"""Deterministic capture redaction/minimization tests (red-first).

These assert the pure `redact_capture` strips planted secrets/PII and minimizes personal
content at capture. This is SUBSTRATE plumbing only — it is not a classifier for arbitrary
private input and makes no learning claim. Fixtures are obviously synthetic.
"""
import json
import unittest
from portfolio_agent.redaction import redact_capture, MAX_FEEDBACK_CHARS


class RedactionTests(unittest.TestCase):
    def test_secrets_and_pii_do_not_survive(self):
        planted = {
            'bearer': 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCD',
            'token': 'tok_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0',
            'password': 'password=hunter2',
            'email': 'contact synthetic.person@example.test please',
            'phone': 'call 415-555-0142 now',
            'ssn': 'ssn 123-45-6789 here',
        }
        result = redact_capture(planted)
        blob = json.dumps(result)
        for raw in ('abcdefghijklmnopqrstuvwxyz0123456789ABCD',
                    'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0',
                    'hunter2', 'synthetic.person@example.test',
                    '415-555-0142', '123-45-6789'):
            self.assertNotIn(raw, blob)
        self.assertIn('[REDACTED:', blob)

    def test_feedback_is_minimized_to_bound(self):
        long_feedback = 'x' * (MAX_FEEDBACK_CHARS + 500)
        result = redact_capture({'feedback': long_feedback})
        self.assertLessEqual(len(result['feedback']), MAX_FEEDBACK_CHARS)

    def test_tool_sequence_keeps_only_names(self):
        data = {'tool_sequence': [
            {'name': 'value_portfolio', 'args': {'portfolio_id': 'p-secret', 'token': 'tok_secret'}},
            {'name': 'list_holdings', 'result': {'AAPL': '10'}},
            'plain_name',
        ]}
        result = redact_capture(data)
        self.assertEqual(result['tool_sequence'], ['value_portfolio', 'list_holdings', 'plain_name'])
        blob = json.dumps(result)
        self.assertNotIn('p-secret', blob)
        self.assertNotIn('tok_secret', blob)
        self.assertNotIn('AAPL', blob)

    def test_is_deterministic(self):
        data = {'feedback': 'Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCD',
                'tool_sequence': [{'name': 'list_holdings', 'args': {'x': 1}}]}
        self.assertEqual(redact_capture(data), redact_capture(data))

    def test_fail_safe_strips_token_shaped_unknown_string(self):
        data = {'note': 'xJ3k' + 'Q7r8S9t0' * 6}  # long base64url-ish blob
        result = redact_capture(data)
        self.assertNotIn('Q7r8S9t0Q7r8S9t0', json.dumps(result))

    def test_keep_path_preserves_structural_ids(self):
        # Owner-scoped structural ids the caller explicitly keeps pass through unchanged.
        data = {'keep': {'portfolio_id': 'p-123', 'symbol': 'DEMO'}}
        result = redact_capture(data)
        self.assertEqual(result['keep'], {'portfolio_id': 'p-123', 'symbol': 'DEMO'})


if __name__ == '__main__':
    unittest.main()
