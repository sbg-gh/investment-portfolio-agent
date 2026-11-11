"""Bind resolution: loopback only unless two explicit overrides plus allowed hosts are given."""
import unittest

from portfolio_agent.web.bind import REMOTE_ACK, BindRefused, resolve_bind


class BindTests(unittest.TestCase):
    def test_loopback_defaults(self):
        self.assertEqual(resolve_bind('127.0.0.1', 8765), ('127.0.0.1', 8765))
        self.assertEqual(resolve_bind('localhost', 8765), ('127.0.0.1', 8765))
        self.assertEqual(resolve_bind('::1', 8765), ('::1', 8765))
        self.assertEqual(resolve_bind('127.0.0.1', 0), ('127.0.0.1', 0))

    def test_non_loopback_refused_by_default(self):
        for host in ('0.0.0.0', '::', '', '192.168.1.5', '10.0.0.1', '127.0.0.2', 'example.com',
                     '[::1]', ' 127.0.0.1', '0', '2130706433', '::ffff:0.0.0.0', None):
            with self.assertRaises(BindRefused, msg=repr(host)):
                resolve_bind(host, 8765)

    def test_flag_alone_or_env_alone_is_not_enough(self):
        with self.assertRaises(BindRefused):
            resolve_bind('0.0.0.0', 8765, allow_remote=True, env={}, allowed_hosts=('a:1',))
        with self.assertRaises(BindRefused):
            resolve_bind('0.0.0.0', 8765, allow_remote=False,
                         env={'PORTFOLIO_AGENT_ALLOW_REMOTE': REMOTE_ACK}, allowed_hosts=('a:1',))
        with self.assertRaises(BindRefused):
            resolve_bind('0.0.0.0', 8765, allow_remote=True,
                         env={'PORTFOLIO_AGENT_ALLOW_REMOTE': 'yes'}, allowed_hosts=('a:1',))

    def test_override_also_requires_explicit_allowed_host(self):
        env = {'PORTFOLIO_AGENT_ALLOW_REMOTE': REMOTE_ACK}
        with self.assertRaises(BindRefused):
            resolve_bind('0.0.0.0', 8765, allow_remote=True, env=env, allowed_hosts=())
        self.assertEqual(resolve_bind('0.0.0.0', 8765, allow_remote=True, env=env,
                                      allowed_hosts=('box.lan:8765',)), ('0.0.0.0', 8765))

    def test_bad_ports_refused(self):
        for port in (-1, 65536, '8765', None, 1.5, True):
            with self.assertRaises(BindRefused, msg=repr(port)):
                resolve_bind('127.0.0.1', port)


if __name__ == '__main__':
    unittest.main()
