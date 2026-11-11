"""Small strict validators shared by route handlers. Failures carry fixed codes only."""
from datetime import datetime, timezone
import re
from urllib.parse import parse_qsl

from .wire import WireError, decimal_string

_KEY = re.compile(r'[A-Za-z0-9_-]{8,128}', re.ASCII)
_SYMBOL = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]{0,14}', re.ASCII)


def text(value, *, max_len=128):
    if not isinstance(value, str) or not value.strip() or len(value) > max_len or not value.isprintable():
        raise WireError('invalid_field')
    return value


def symbol(value):
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise WireError('invalid_field')
    return value


def idempotency_key(value):
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise WireError('invalid_field')
    return value


def decimal(value, field):
    return decimal_string(value, field)


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise WireError('invalid_field')
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise WireError('invalid_field') from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise WireError('invalid_field')
    return moment.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc)


def query(request, allowed):
    """Parse the query string: only allowed names, each at most once, bounded values."""
    try:
        pairs = parse_qsl(request.query, keep_blank_values=True, strict_parsing=bool(request.query),
                          max_num_fields=8)
    except ValueError:
        raise WireError('invalid_query') from None
    found = {}
    for name, value in pairs:
        if name not in allowed or name in found or len(value) > 1500:
            raise WireError('invalid_query')
        found[name] = value
    return found
