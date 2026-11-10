"""reauthenticate: a browser session keeps only a credential id and mints a fresh context."""
import unittest
from dataclasses import replace

from portfolio_agent.auth import Authenticator
from portfolio_agent.core import Store


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ReauthenticateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = Store()
        self.addCleanup(self.store.close)
        for user in ('alice', 'admin'):
            self.store.create_user(user)
        self.auth = Authenticator(self.store, clock=self.clock)
        self.token = self.auth.provision('alice', ttl_seconds=3600)
        self.op_token = self.auth.provision('admin', role='operator', ttl_seconds=3600)

    def credential_id(self, token):
        return self.auth.authenticate(token).credential_id

    def test_returns_verifiable_context_and_role(self):
        cid = self.credential_id(self.token)
        context, role = self.auth.reauthenticate(cid)
        self.assertEqual((context.user_id, context.credential_id, role), ('alice', cid, 'user'))
        self.assertEqual(self.auth.require(context), 'alice')

    def test_operator_role_is_reported_from_the_store(self):
        context, role = self.auth.reauthenticate(self.credential_id(self.op_token))
        self.assertEqual(role, 'operator')
        self.assertEqual(self.auth.require(context, operator=True), 'admin')

    def test_each_call_mints_a_new_request_id(self):
        cid = self.credential_id(self.token)
        self.assertNotEqual(self.auth.reauthenticate(cid)[0].request_id,
                            self.auth.reauthenticate(cid)[0].request_id)

    def test_revoked_credential_denied(self):
        cid = self.credential_id(self.token)
        self.auth.revoke(cid)
        with self.assertRaises(PermissionError):
            self.auth.reauthenticate(cid)

    def test_expired_credential_denied(self):
        cid = self.credential_id(self.token)
        self.clock.now += 3601
        with self.assertRaises(PermissionError):
            self.auth.reauthenticate(cid)

    def test_unknown_or_malformed_credential_denied_with_one_message(self):
        messages = set()
        for bad in ('nope', '', None, 7, 'x' * 500, "' OR 1=1 --"):
            with self.assertRaises(PermissionError) as caught:
                self.auth.reauthenticate(bad)
            messages.add(str(caught.exception))
        self.assertEqual(len(messages), 1)

    def test_context_expiry_is_capped_to_five_minutes(self):
        context, _ = self.auth.reauthenticate(self.credential_id(self.token))
        self.assertLessEqual(context.expires_at, self.clock.now + 300)

    def test_reauthenticated_context_cannot_be_forged_upward(self):
        context, _ = self.auth.reauthenticate(self.credential_id(self.token))
        with self.assertRaises(PermissionError):
            self.auth.require(replace(context, user_id='admin'))


if __name__ == '__main__':
    unittest.main()
