"""Strict JSON wire format: money and quantities travel as decimal strings, never numbers."""
from decimal import Decimal
import unittest

from portfolio_agent.web import wire
from portfolio_agent.web.wire import WireError


class LoadsTests(unittest.TestCase):
    def code(self, raw):
        with self.assertRaises(WireError) as caught:
            wire.loads(raw)
        return caught.exception.code

    def test_accepts_object_with_strings_bools_null_lists(self):
        self.assertEqual(wire.loads(b'{"a":"1.50","b":true,"c":null,"d":["x"]}'),
                         {'a': '1.50', 'b': True, 'c': None, 'd': ['x']})

    def test_rejects_every_json_number_form(self):
        for raw in (b'{"q":1}', b'{"q":1.5}', b'{"q":-0}', b'{"q":1e3}', b'{"q":0.1}',
                    b'{"q":[1]}', b'{"q":{"n":2}}', b'{"q":NaN}', b'{"q":Infinity}',
                    b'{"q":-Infinity}', b'{"q":' + b'9' * 400 + b'}'):
            self.assertEqual(self.code(raw), 'invalid_number', raw[:30])

    def test_rejects_malformed_json(self):
        for raw in (b'', b'{', b'{"a":}', b"{'a':'b'}", b'{"a":"b"} trailing', b'\x00', b'null{}'):
            self.assertEqual(self.code(raw), 'invalid_json', raw)

    def test_rejects_non_utf8_and_bom_and_other_encodings(self):
        self.assertEqual(self.code(b'{"a":"\xff"}'), 'invalid_json')
        self.assertEqual(self.code(b'\xef\xbb\xbf{"a":"b"}'), 'invalid_json')
        self.assertEqual(self.code('{"a":"b"}'.encode('utf-16')), 'invalid_json')

    def test_rejects_non_object_top_level(self):
        for raw in (b'[]', b'"x"', b'true', b'null'):
            self.assertEqual(self.code(raw), 'invalid_body')

    def test_rejects_duplicate_keys(self):
        self.assertEqual(self.code(b'{"a":"1","a":"2"}'), 'invalid_body')

    def test_rejects_pathological_nesting_without_crashing(self):
        self.assertEqual(self.code(b'{"a":' * 5000 + b'"x"' + b'}' * 5000), 'invalid_body')

    def test_rejects_oversized_input(self):
        self.assertEqual(self.code(b'{"a":"' + b'x' * wire.MAX_BODY + b'"}'), 'body_too_large')

    def test_error_text_never_echoes_input(self):
        with self.assertRaises(WireError) as caught:
            wire.loads(b'{"token":"s3cret-value" nope')
        self.assertNotIn('s3cret', repr(caught.exception) + str(caught.exception))


class DecimalStringTests(unittest.TestCase):
    def test_accepts_plain_decimal_text(self):
        for value in ('0', '12', '0.5', '1234567.890123456789'):
            self.assertEqual(wire.decimal_string(value, 'quantity'), value)

    def test_rejects_everything_else(self):
        for value in ('', '-1', '+1', '1e3', '1.', '.5', ' 1', '1 ', '1,000', 'NaN', 'Infinity',
                      '0x10', '１２', '1' * 41, 1, 1.5, None, True, Decimal('1'), ['1'], '1\n'):
            with self.assertRaises(WireError, msg=repr(value)) as caught:
                wire.decimal_string(value, 'quantity')
            self.assertEqual(caught.exception.code, 'invalid_field')


class DumpsTests(unittest.TestCase):
    def test_decimal_serializes_as_text_never_a_number(self):
        self.assertEqual(wire.dumps({'v': Decimal('10.50')}), b'{"v":"10.50"}')

    def test_floats_and_nan_are_refused(self):
        for value in (1.5, float('nan'), {'a': [2.0]}):
            with self.assertRaises(TypeError):
                wire.dumps({'v': value})

    def test_compact_utf8_output(self):
        self.assertEqual(wire.dumps({'a': 'é'}), '{"a":"é"}'.encode())


if __name__ == '__main__':
    unittest.main()
