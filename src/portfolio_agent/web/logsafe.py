"""Log lines built from fixed fields only: never paths, queries, headers, bodies, ids or cookies."""
import re

_METHODS = frozenset({'GET', 'POST', 'DELETE', 'PUT', 'PATCH', 'HEAD', 'OPTIONS'})
_TEMPLATE = re.compile(r'/[A-Za-z0-9/_{}.-]{0,200}')
_RID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
_CLASS = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}')


def _rid(value):
    return value if isinstance(value, str) and _RID.fullmatch(value) else '-'


def access_line(method, template, status, duration_ms, request_id):
    method = method if method in _METHODS else 'OTHER'
    template = template if isinstance(template, str) and _TEMPLATE.fullmatch(template) else '-'
    status = status if isinstance(status, int) else 0
    return f'{method} {template} {status} {int(duration_ms)}ms {_rid(request_id)}'


def error_line(exc, request_id):
    name = type(exc).__name__
    return f'ERROR {name if _CLASS.fullmatch(name) else "Exception"} {_rid(request_id)}'
