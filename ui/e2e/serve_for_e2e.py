"""Test-only launcher for the browser smoke test (ui/e2e/smoke.mjs). Never use it to serve real data.

It creates a throwaway database in the system temp directory, two obviously synthetic users (one
user, one operator), and serves the real web application on an ephemeral loopback port with an
offline scripted model, so no Ollama or network is needed. The first stdout line is JSON with the
URL and the two bearer tokens; the temp directory is deleted when the process exits.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import Store
from portfolio_agent.web.server import build_server

USER_ID = 'e2e-user'
OPERATOR_ID = 'e2e-operator'


class E2EModel:
    """Offline stand-in for the local model. Always returns one well-formed, tool-free reply."""

    def generate(self, prompt, *, max_tokens):
        return json.dumps({'explanation': 'E2E scripted reply: this text comes from a fixed script, not a model.',
                           'tool_calls': []})


def prepare(directory):
    """Create the temp database and issue one user and one operator token. Returns (db_path, tokens)."""
    root = Path(os.path.realpath(directory))
    temp = Path(os.path.realpath(tempfile.gettempdir()))
    if temp not in root.parents:
        raise ValueError('the e2e database must live in a temporary directory')
    db = root / 'e2e.db'
    store = Store(str(db))
    try:
        auth = Authenticator(store)
        tokens = {}
        for role, user_id in (('user', USER_ID), ('operator', OPERATOR_ID)):
            store.create_user(user_id)
            tokens[role] = auth.provision(user_id, role=role, ttl_seconds=3600)
    finally:
        store.close()
    return db, tokens


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def main():
    directory = tempfile.mkdtemp(prefix='pa-e2e-')
    try:
        db, tokens = prepare(directory)
        port = free_port()
        server, _ = build_server(str(db), host='127.0.0.1', port=port, model=E2EModel(),
                                 valuation_source='operator-entered', log=lambda line: None)
        print(json.dumps({'url': f'http://127.0.0.1:{port}', 'tokens': tokens}), flush=True)
        server.run()
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
