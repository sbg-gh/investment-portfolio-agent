"""Deterministic capture redaction/minimization for M5a run records.

SUBSTRATE ONLY. `redact_capture` runs at capture BEFORE any `run_records` write so planted
secrets/PII never persist and personal free-text is minimized. It is conservative and
fail-safe: when a value matches any credential/PII pattern it is stripped. It redacts ONLY
the captured fields it is handed; it is NOT a general classifier for arbitrary private input
and it makes NO learning/adaptation claim. Pure function, no model and no network.
"""
import re

# Named bounds/placeholders (no numeric separators, plain integers).
MAX_FEEDBACK_CHARS = 280
PLACEHOLDER = '[REDACTED:{kind}]'

# Keys whose values are preserved verbatim because the caller marks them structural/owner-scoped.
_KEEP_KEY = 'keep'
# Keys that are minimized rather than secret-scanned.
_FEEDBACK_KEY = 'feedback'
_TOOL_SEQUENCE_KEY = 'tool_sequence'

# Ordered secret/PII patterns; first match wins so the placeholder kind is deterministic.
_PATTERNS = (
    ('ssn', re.compile(r'\b\d{3}-\d{2}-\d{4}\b')),
    ('email', re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')),
    ('phone', re.compile(r'\b\d{3}-\d{3}-\d{4}\b')),
    ('authorization', re.compile(r'(?i)authorization:\s*bearer\s+\S+')),
    ('credential', re.compile(r'(?i)(?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+')),
    # Long credential/token-shaped runs of base64url/hex characters.
    ('token', re.compile(r'\b[A-Za-z0-9_\-]{32,}\b')),
)


def _redact_text(value: str) -> str:
    redacted = value
    for kind, pattern in _PATTERNS:
        redacted = pattern.sub(PLACEHOLDER.format(kind=kind), redacted)
    return redacted


def _reduce_tool_sequence(sequence):
    """Keep only tool NAMES; drop args/results entirely so no payload survives."""
    names = []
    for entry in sequence:
        if isinstance(entry, dict):
            name = entry.get('name')
            names.append(_redact_text(name) if isinstance(name, str) else '[REDACTED:tool]')
        elif isinstance(entry, str):
            names.append(_redact_text(entry))
        else:
            names.append('[REDACTED:tool]')
    return names


def redact_capture(data: dict) -> dict:
    """Return a redacted/minimized copy of the captured fields. Deterministic and pure."""
    result = {}
    for key, value in data.items():
        if key == _KEEP_KEY:
            result[key] = value
        elif key == _TOOL_SEQUENCE_KEY and isinstance(value, (list, tuple)):
            result[key] = _reduce_tool_sequence(value)
        elif key == _FEEDBACK_KEY and isinstance(value, str):
            result[key] = _redact_text(value)[:MAX_FEEDBACK_CHARS]
        elif isinstance(value, str):
            result[key] = _redact_text(value)
        else:
            result[key] = value
    return result
