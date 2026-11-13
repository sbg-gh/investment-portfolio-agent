"""Operator CSV price import (stdlib only; no network, no provider).

Required columns (exact names, case-insensitive, any order): symbol, date, close. Every other
column is ignored: only its sanitized NAME is reported, its values are never read or stored.
`date` is a YYYY-MM-DD session date mapped to 16:00 America/New_York (UTC via zoneinfo); there is
no holiday calendar. `close` is a plain decimal string parsed with Decimal, never float.

Results carry only fixed codes, 1-based file line numbers and symbols that match the symbol
pattern: never file content, values or exception text. Prices for unregistered symbols are
rejected; nothing is ever auto-registered. Rows go through valuation.ingest_observation.
"""
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import io
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import valuation
from .ports import Observation

MAX_BYTES = 1048576
MAX_ROWS = 10000
MAX_IGNORED_COLUMNS = 20
MAX_COLUMN_NAME = 32
MAX_CLOSE_CHARS = 30
REQUIRED = ('symbol', 'date', 'close')
EXCHANGE_TZ = 'America/New_York'
_SOURCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', re.ASCII)
_SYMBOL = re.compile(r'[A-Z0-9][A-Z0-9.-]{0,14}', re.ASCII)
_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', re.ASCII)
_CLOSE = re.compile(r'[0-9]+(\.[0-9]+)?', re.ASCII)


class FileError(ValueError):
    """File-level rejection carrying a fixed code (never file content)."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedRow:
    line: int
    symbol: str
    date: str
    close: str


@dataclass(frozen=True)
class ParsedFile:
    rows: list
    ignored_columns: list


@dataclass
class RowResult:
    line: int
    symbol: str | None
    outcome: str
    security_id: str | None = None
    observed_at: datetime | None = None
    value: Decimal | None = None


def close_to_utc(day):
    """16:00 America/New_York on the session date, as UTC."""
    try:
        zone = ZoneInfo(EXCHANGE_TZ)
    except ZoneInfoNotFoundError:
        raise FileError('timezone_unavailable') from None
    return datetime(day.year, day.month, day.day, 16, tzinfo=zone).astimezone(timezone.utc)


def _label(name):
    clean = ''.join(ch if ch.isascii() and ch.isprintable() else '?' for ch in name.strip().lower())
    return clean[:MAX_COLUMN_NAME]


def _decode(data):
    if isinstance(data, str):
        if len(data.encode('utf-8', 'surrogatepass')) > MAX_BYTES:
            raise FileError('file_too_large')
        text = data
    elif isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_BYTES:
            raise FileError('file_too_large')
        try:
            text = bytes(data).decode('utf-8')
        except UnicodeDecodeError:
            raise FileError('invalid_encoding') from None
    else:
        raise FileError('invalid_encoding')
    if '\x00' in text:
        raise FileError('invalid_csv')
    return text[1:] if text.startswith('\ufeff') else text


def parse_csv(data):
    text = _decode(data)
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    position, ignored, rows, previous = None, [], [], 0
    try:
        for record in reader:
            line, previous = previous + 1, reader.line_num
            if not record:
                continue
            if position is None:
                names = [cell.strip().lower() for cell in record]
                if any(names.count(name) != 1 for name in REQUIRED):
                    raise FileError('invalid_header')
                position = {name: names.index(name) for name in REQUIRED}
                for cell, name in zip(record, names):
                    label = _label(cell)
                    if name not in REQUIRED and label and label not in ignored:
                        ignored.append(label)
                continue
            if len(rows) >= MAX_ROWS:
                raise FileError('too_many_rows')
            cells = {name: record[index] if index < len(record) else ''
                     for name, index in position.items()}
            rows.append(ParsedRow(line, cells['symbol'], cells['date'], cells['close']))
    except csv.Error:
        raise FileError('invalid_csv') from None
    if not rows:
        raise FileError('empty_file')
    return ParsedFile(rows, ignored[:MAX_IGNORED_COLUMNS])


def _symbol(raw):
    symbol = raw.strip().upper()
    return symbol if _SYMBOL.fullmatch(symbol) else None


def _parse_date(raw):
    text = raw.strip()
    if not _DATE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_close(raw):
    text = raw.strip()
    if len(text) > MAX_CLOSE_CHARS or not _CLOSE.fullmatch(text):
        return None
    return Decimal(text)


def _classify(row, symbol, close, duplicated, store, now, lookup):
    if symbol is None or close is None:
        return RowResult(row.line, symbol, 'invalid_format')
    if duplicated:
        return RowResult(row.line, symbol, 'duplicate_row')
    day = _parse_date(row.date)
    if day is None:
        return RowResult(row.line, symbol, 'invalid_date')
    observed = close_to_utc(day)
    if observed > now:
        return RowResult(row.line, symbol, 'future_date')
    try:
        if close == 0:
            raise ValueError
        valuation.numeric(close)
    except ValueError:
        return RowResult(row.line, symbol, 'invalid_value')
    if symbol not in lookup:
        found = store.db.execute('SELECT id,kind FROM securities WHERE symbol=?', (symbol,)).fetchone()
        lookup[symbol] = found
    found = lookup[symbol]
    if found is None:
        return RowResult(row.line, symbol, 'unknown_symbol')
    if found[1] == 'mutual_fund':
        return RowResult(row.line, symbol, 'unsupported_kind')
    return RowResult(row.line, symbol, 'imported', found[0], observed, close)


def plan_import(store, parsed, source, now):
    """Classify every row. Pure reads: nothing is written."""
    prepared = [(row, _symbol(row.symbol), _parse_close(row.close)) for row in parsed.rows]
    seen = {}
    for row, symbol, _ in prepared:
        if symbol is not None:
            key = (symbol, row.date.strip())
            seen[key] = seen.get(key, 0) + 1
    lookup, results = {}, []
    for row, symbol, close in prepared:
        duplicated = symbol is not None and seen[(symbol, row.date.strip())] > 1
        result = _classify(row, symbol, close, duplicated, store, now, lookup)
        if result.outcome == 'imported':
            latest = store.db.execute(
                'SELECT value FROM market_observations WHERE security_id=? AND source=? AND basis=? '
                'AND observed_at=? ORDER BY fetched_at DESC,id DESC LIMIT 1',
                (result.security_id, source, 'price', result.observed_at.isoformat())).fetchone()
            if latest is not None:
                same = latest[0] == valuation.decimal_text(result.value)
                result.outcome = 'unchanged' if same else 'corrected'
        results.append(result)
    return results


def import_prices(store, csv_input, source, *, dry_run, now=None):
    if not isinstance(source, str) or not _SOURCE.fullmatch(source):
        raise FileError('invalid_source')
    parsed = parse_csv(csv_input)
    return import_rows(store, parsed.rows, source, dry_run=dry_run, now=now,
                       ignored_columns=parsed.ignored_columns)
def import_rows(store, rows, source, *, dry_run, now=None, ignored_columns=()):
    """Validate and ingest ParsedRow objects (the CSV path and the Yahoo fetch share this core)."""
    if not isinstance(source, str) or not _SOURCE.fullmatch(source):
        raise FileError('invalid_source')
    if not rows:
        raise FileError('empty_file')
    if len(rows) > MAX_ROWS:
        raise FileError('too_many_rows')
    parsed = ParsedFile(list(rows), list(ignored_columns))
    now = valuation.utc(now) if now is not None else datetime.now(timezone.utc)
    with store.transaction():
        results = plan_import(store, parsed, source, now)
        if not dry_run:
            for result in results:
                if result.outcome not in ('imported', 'corrected'):
                    continue
                try:
                    valuation.ingest_observation(store, Observation(
                        result.security_id, result.value, result.observed_at, source, 'price',
                        fetched_at=now))
                except ValueError:
                    result.outcome = 'conflict'
    counts = {}
    for result in results:
        counts[result.outcome] = counts.get(result.outcome, 0) + 1
    return {'dry_run': dry_run, 'source': source, 'total_rows': len(results), 'counts': counts,
            'ignored_columns': parsed.ignored_columns,
            'rows': [{'line': r.line, 'symbol': r.symbol, 'outcome': r.outcome} for r in results]}
