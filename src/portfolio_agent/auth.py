"""Local bearer credentials and tamper-evident request contexts.

Provisioning is a trusted operator operation. No HTTP transport is supplied.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import secrets
import time
from uuid import uuid4

@dataclass(frozen=True)
class AuthContext:
    user_id: str
    credential_id: str
    request_id: str
    expires_at: float
    signature: str = field(repr=False)

class Authenticator:
    def __init__(self, store, clock=time.time):
        self._store = store
        self._clock = clock
        self._key = secrets.token_bytes(32)

    def provision(self, user_id, *, role='user', ttl_seconds=86400):
        """Trusted local administration only; return the token once, never log it."""
        if role not in {'user','operator'} or not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError('Invalid credential policy')
        if not self._store.db.execute('SELECT 1 FROM users WHERE id=?',(user_id,)).fetchone():
            raise PermissionError('Unknown user')
        token = secrets.token_urlsafe(32)
        with self._store.db:
            self._store.db.execute('INSERT INTO credentials(id,user_id,token_hash,role,expires_at) VALUES (?,?,?,?,?)',
                (str(uuid4()),user_id,hashlib.sha256(token.encode()).hexdigest(),role,self._clock()+ttl_seconds))
        return token

    def _signature(self, user_id, credential_id, request_id, expires_at):
        message = json.dumps([user_id,credential_id,request_id,expires_at],separators=(',',':')).encode()
        return hmac.new(self._key,message,hashlib.sha256).hexdigest()

    def authenticate(self, token):
        if not isinstance(token,str) or not token or len(token)>256:
            raise PermissionError('Invalid credentials')
        row = self._store.db.execute(
            'SELECT id,user_id,expires_at FROM credentials WHERE token_hash=? AND revoked=0 AND expires_at>?',
            (hashlib.sha256(token.encode()).hexdigest(),self._clock())).fetchone()
        if row is None:
            raise PermissionError('Invalid credentials')
        credential_id,user_id,expiry = row
        request_id = str(uuid4())
        expiry = min(expiry,self._clock()+300)
        return AuthContext(user_id,credential_id,request_id,expiry,
                           self._signature(user_id,credential_id,request_id,expiry))

    def reauthenticate(self, credential_id):
        """Mint a fresh short-lived context for a live credential; return (context, role).

        A web session stores only the credential id (never the bearer token). Every request
        re-checks revocation and expiry here, so revoking a credential ends its sessions.
        """
        if not isinstance(credential_id,str) or not credential_id or len(credential_id)>128:
            raise PermissionError('Invalid credentials')
        row = self._store.db.execute(
            'SELECT user_id,role,expires_at FROM credentials WHERE id=? AND revoked=0 AND expires_at>?',
            (credential_id,self._clock())).fetchone()
        if row is None:
            raise PermissionError('Invalid credentials')
        user_id,role,expiry = row
        request_id = str(uuid4())
        expiry = min(expiry,self._clock()+300)
        return (AuthContext(user_id,credential_id,request_id,expiry,
                            self._signature(user_id,credential_id,request_id,expiry)),role)

    def require(self, context, *, operator=False):
        if not isinstance(context,AuthContext):
            raise PermissionError('Authenticated context required')
        expected = self._signature(context.user_id,context.credential_id,context.request_id,context.expires_at)
        if not isinstance(context.signature,str) or not hmac.compare_digest(expected,context.signature):
            raise PermissionError('Invalid context')
        if context.expires_at <= self._clock():
            raise PermissionError('Expired context')
        row = self._store.db.execute(
            'SELECT role FROM credentials WHERE id=? AND user_id=? AND revoked=0 AND expires_at>?',
            (context.credential_id,context.user_id,self._clock())).fetchone()
        if row is None or (operator and row[0]!='operator'):
            raise PermissionError('Access denied')
        return context.user_id

    def revoke(self, credential_id):
        """Trusted local administration. Existing contexts stop working immediately."""
        with self._store.db:
            self._store.db.execute('UPDATE credentials SET revoked=1 WHERE id=?',(credential_id,))
