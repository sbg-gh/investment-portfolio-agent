"""Host, Origin, content-type and header-shape policy for the loopback listener."""
import re

from .wire import MAX_BODY

MAX_HEADERS = 64
MAX_NAME = 64
MAX_VALUE = 8192
MAX_TOTAL = 32768
SINGLETONS = frozenset({'host', 'origin', 'content-type', 'content-length', 'cookie',
                        'x-csrf-token', 'authorization', 'sec-fetch-site', 'transfer-encoding'})
_CONTENT_LENGTH = re.compile(r'[0-9]{1,12}', re.ASCII)
_CONTROL = re.compile(r'[\x00-\x08\x0a-\x1f\x7f]')
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class PolicyError(Exception):
    CODES = {400: 'bad_request', 403: 'forbidden', 413: 'body_too_large',
             415: 'unsupported_media_type', 431: 'headers_too_large'}

    def __init__(self, status):
        super().__init__(status)
        self.status = status
        self.code = self.CODES[status]


class Policy:
    def __init__(self, port, extra_hosts=()):
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}
        hosts.update(host.lower() for host in extra_hosts)
        self.hosts = frozenset(hosts)
        self.origins = frozenset('http://' + host for host in hosts)

    def check_host(self, host):
        if not isinstance(host, str) or len(host) > 255 or host.lower() not in self.hosts:
            raise PolicyError(403)

    def check_origin(self, origin, *, required):
        if origin is None:
            if required:
                raise PolicyError(403)
            return
        if not isinstance(origin, str) or origin not in self.origins:
            raise PolicyError(403)

    @staticmethod
    def check_fetch_site(site):
        if site not in (None, 'same-origin', 'none'):
            raise PolicyError(403)


def check_content_type(value):
    if not isinstance(value, str):
        raise PolicyError(415)
    parts = [part.strip().lower() for part in value.split(';')]
    if parts[0] != 'application/json' or any(p not in ('charset=utf-8',) for p in parts[1:]):
        raise PolicyError(415)


def check_headers(headers):
    if len(headers) > MAX_HEADERS:
        raise PolicyError(431)
    total = 0
    seen = set()
    duplicate = False
    for name, value in headers:
        if len(name) > MAX_NAME or len(value) > MAX_VALUE:
            raise PolicyError(431)
        total += len(name) + len(value)
        if name in SINGLETONS:
            duplicate = duplicate or name in seen
            seen.add(name)
    if total > MAX_TOTAL:
        raise PolicyError(431)
    for name, value in headers:
        if _CONTROL.search(name) or _CONTROL.search(value):
            raise PolicyError(400)
    if duplicate:
        raise PolicyError(400)
    for name, value in headers:
        if name == 'content-length':
            if not _CONTENT_LENGTH.fullmatch(value):
                raise PolicyError(400)
            if int(value) > MAX_BODY:
                raise PolicyError(413)


def response_headers(*, api):
    return [('cache-control', 'no-store' if api else 'no-cache'),
            ('content-security-policy', CSP),
            ('x-content-type-options', 'nosniff'),
            ('referrer-policy', 'no-referrer'),
            ('x-frame-options', 'DENY'),
            ('cross-origin-resource-policy', 'same-origin')]
