"""Listener bind policy: loopback only unless two explicit overrides and allowed hosts are given."""
import os

REMOTE_ACK = 'I-UNDERSTAND-THERE-IS-NO-TLS'
REMOTE_ENV = 'PORTFOLIO_AGENT_ALLOW_REMOTE'


class BindRefused(Exception):
    pass


def resolve_bind(host, port, allow_remote=False, env=None, allowed_hosts=()):
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise BindRefused('Invalid port')
    if not isinstance(host, str):
        raise BindRefused('Invalid host')
    if host == 'localhost':
        return '127.0.0.1', port
    if host in ('127.0.0.1', '::1'):
        return host, port
    env = os.environ if env is None else env
    if not (allow_remote and env.get(REMOTE_ENV) == REMOTE_ACK and allowed_hosts):
        raise BindRefused('Non-loopback bind refused: requires --allow-non-loopback, '
                          f'{REMOTE_ENV}={REMOTE_ACK} and an explicit --allowed-host. There is no TLS.')
    return host, port
