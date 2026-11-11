"""Strict JSON wire format. Money and quantities are decimal strings, never JSON numbers."""
from decimal import Decimal
import json
import re

MAX_BODY = 65536
MAX_DECIMAL_LENGTH = 40
_DECIMAL = re.compile(r'[0-9]+(\.[0-9]+)?', re.ASCII)


class WireError(Exception):
    """Carries only a fixed code; never echoes caller input."""
    def __init__(self, code, status=422):
        super().__init__(code)
        self.code = code
        self.status = status


def _reject_number(_text):
    raise WireError('invalid_number')


def _object(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise WireError('invalid_body')
    return dict(pairs)


def loads(raw):
    if not isinstance(raw, (bytes, bytearray)):
        raise WireError('invalid_json', 400)
    if len(raw) > MAX_BODY:
        raise WireError('body_too_large', 413)
    try:
        text = bytes(raw).decode('utf-8')
        value = json.loads(text, parse_float=_reject_number, parse_int=_reject_number,
                           parse_constant=_reject_number, object_pairs_hook=_object)
    except WireError:
        raise
    except RecursionError:
        raise WireError('invalid_body') from None
    except (ValueError, UnicodeDecodeError):
        raise WireError('invalid_json', 400) from None
    if not isinstance(value, dict):
        raise WireError('invalid_body')
    return value


def decimal_string(value, field):
    if (not isinstance(value, str) or len(value) > MAX_DECIMAL_LENGTH
            or not _DECIMAL.fullmatch(value)):
        raise WireError('invalid_field')
    return value


def _plain(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, float):
        raise TypeError('floats are not allowed on the wire')
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def dumps(value):
    return json.dumps(_plain(value), separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')
