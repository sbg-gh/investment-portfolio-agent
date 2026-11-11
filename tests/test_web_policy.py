"""Host/Origin/header policy for the loopback listener."""
import unittest

from portfolio_agent.web import policy
from portfolio_agent.web.policy import Policy, PolicyError


class HostTests(unittest.TestCase):
    def setUp(self):
        self.p = Policy(port=8765)

    def ok(self, host):
        self.p.check_host(host)

    def bad(self, host):
        with self.assertRaises(PolicyError) as caught:
            self.p.check_host(host)
        self.assertEqual((caught.exception.status, caught.exception.code), (403, 'forbidden'))

    def test_loopback_names_with_exact_port_pass(self):
        for host in ('127.0.0.1:8765', 'localhost:8765', '[::1]:8765', 'LOCALHOST:8765'):
            self.ok(host)

    def test_rebinding_and_lookalike_hosts_fail(self):
        for host in ('evil.example:8765', 'evil.localhost:8765', 'localhost.evil.com:8765',
                     'localhost:8765.evil.com', '127.0.0.1', 'localhost', '127.0.0.1:80',
                     '127.0.0.2:8765', '0.0.0.0:8765', '[::]:8765', '2130706433:8765',
                     '127.1:8765', '0x7f.0.0.1:8765', 'localhost:8765 ', ' localhost:8765',
                     'localhost:8765/', 'user@localhost:8765', 'localhost:8765,evil.com',
                     'localhost%00:8765', '', None, 'a' * 5000):
            self.bad(host)

    def test_explicit_extra_hosts_are_exact(self):
        p = Policy(port=1, extra_hosts=('box.lan:9000',))
        p.check_host('box.lan:9000')
        with self.assertRaises(PolicyError):
            p.check_host('other.lan:9000')


class OriginTests(unittest.TestCase):
    def setUp(self):
        self.p = Policy(port=8765)

    def test_unsafe_requires_exact_allowed_origin(self):
        for origin in ('http://127.0.0.1:8765', 'http://localhost:8765', 'http://[::1]:8765'):
            self.p.check_origin(origin, required=True)

    def test_missing_origin_on_unsafe_is_refused_but_optional_on_safe(self):
        with self.assertRaises(PolicyError):
            self.p.check_origin(None, required=True)
        self.p.check_origin(None, required=False)

    def test_foreign_origins_refused_even_when_optional(self):
        for origin in ('null', 'http://evil.example', 'https://localhost:8765', 'http://localhost',
                       'http://localhost:8766', 'http://localhost:8765.evil.com', 'http://evil.localhost:8765',
                       'http://localhost:8765/', 'http://localhost:8765/x', 'file://', '', 'http://user@localhost:8765'):
            for required in (True, False):
                with self.assertRaises(PolicyError, msg=origin):
                    self.p.check_origin(origin, required=required)

    def test_fetch_metadata_cross_site_refused_on_unsafe(self):
        for site in ('cross-site', 'same-site', 'bogus'):
            with self.assertRaises(PolicyError):
                self.p.check_fetch_site(site)
        for site in (None, 'same-origin', 'none'):
            self.p.check_fetch_site(site)


class ContentTypeTests(unittest.TestCase):
    def test_json_only(self):
        for value in ('application/json', 'application/json; charset=utf-8', 'Application/JSON'):
            policy.check_content_type(value)
        for value in (None, '', 'text/plain', 'application/x-www-form-urlencoded',
                      'multipart/form-data; boundary=x', 'application/json-patch+json',
                      'application/json; charset=utf-16', 'application/jsonx', 'text/html'):
            with self.assertRaises(PolicyError) as caught:
                policy.check_content_type(value)
            self.assertEqual(caught.exception.status, 415)


class HeaderShapeTests(unittest.TestCase):
    def check(self, headers):
        policy.check_headers([(k.lower(), v) for k, v in headers])

    def err(self, headers):
        with self.assertRaises(PolicyError) as caught:
            self.check(headers)
        return caught.exception.status

    def test_normal_headers_pass(self):
        self.check([('Host', 'localhost:1'), ('Accept', '*/*'), ('Cookie', 'a=b')])

    def test_duplicate_singleton_headers_refused(self):
        for name in ('Host', 'Origin', 'Content-Type', 'Content-Length', 'Cookie', 'X-CSRF-Token',
                     'Authorization', 'Sec-Fetch-Site'):
            self.assertEqual(self.err([(name, 'a'), (name, 'a')]), 400, name)

    def test_oversized_header_value_name_count_and_total(self):
        self.assertEqual(self.err([('X-A', 'v' * 9000)]), 431)
        self.assertEqual(self.err([('x' * 200, 'v')]), 431)
        self.assertEqual(self.err([(f'x-{i}', 'v') for i in range(100)]), 431)
        self.assertEqual(self.err([(f'x-{i}', 'v' * 4000) for i in range(10)]), 431)

    def test_control_characters_refused(self):
        for value in ('a\r\nX-Injected: 1', 'a\nb', 'a\x00b', 'a\x7fb'):
            self.assertEqual(self.err([('X-A', value)]), 400)

    def test_content_length_must_be_plain_digits_within_limit(self):
        for value in ('-1', '1.5', '1e3', ' 5', '0x10', '', '+5', '١٢'):
            self.assertEqual(self.err([('Content-Length', value)]), 400, value)
        self.assertEqual(self.err([('Content-Length', str(10**9))]), 413)
        self.check([('Content-Length', '12')])


class ResponseHeaderTests(unittest.TestCase):
    def test_api_responses_are_no_store_with_csp_and_never_cors(self):
        headers = dict(policy.response_headers(api=True))
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertIn("default-src 'none'", headers['content-security-policy'])
        self.assertIn("script-src 'self'", headers['content-security-policy'])
        self.assertIn("frame-ancestors 'none'", headers['content-security-policy'])
        self.assertEqual(headers['x-content-type-options'], 'nosniff')
        self.assertEqual(headers['referrer-policy'], 'no-referrer')
        self.assertEqual(headers['x-frame-options'], 'DENY')
        self.assertFalse([k for k in headers if k.startswith('access-control-')])
        self.assertNotIn('unsafe-inline', headers['content-security-policy'])
        self.assertNotIn('unsafe-eval', headers['content-security-policy'])


if __name__ == '__main__':
    unittest.main()
