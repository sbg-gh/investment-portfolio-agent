"""One manual Yahoo press, shared by the CLI and the web fetch manager (stdlib only).

Three phases, so the network never runs inside a store transaction:
  1. read_targets(store): a short read of the registered stock/ETF symbols (funds are never requested);
  2. yahoo_chart.fetch_press(...): network only, no store;
  3. ingest(rows, dry_run): ONE short import through price_import.import_rows (source `yahoo-chart`),
     so unchanged / corrected / atomic semantics are exactly the CSV importer's.
Results carry fixed outcome codes, counts and dates/closes only: never provider or exception text.
"""
from . import yahoo_chart
from .price_import import ParsedRow
SOURCE = yahoo_chart.SOURCE
ACCEPTED = ('imported', 'unchanged', 'corrected')
_REJECT_MAP = {'unknown_symbol': 'not_found', 'unsupported_kind': 'unsupported_kind'}
HOOKS = {}   # tests may set transport / clock / sleeper / rng for the CLI path (never set in production)
def read_targets(store):
    """Registered price-basis instruments (stock, ETF), sorted. Mutual funds (NAV) are out of scope."""
    return [row[0] for row in store.db.execute(
        "SELECT symbol FROM securities WHERE kind IN ('stock','etf') ORDER BY symbol").fetchall()]
def merge(outcomes):
    """Per-symbol outcome from its per-date row outcomes: corrected > imported > unchanged."""
    for code in ('corrected', 'imported', 'unchanged'):
        if code in outcomes:
            return code
    return 'invalid_response'
def run_press(targets, ingest, *, dry_run, transport=None, clock=None, sleeper=None, rng=None,
              should_stop=None, on_progress=None):
    fetched = yahoo_chart.fetch_press(targets, registered=set(targets), transport=transport, clock=clock,
                                      sleeper=sleeper, rng=rng, should_stop=should_stop,
                                      on_progress=on_progress)
    rows, owner = [], []
    for entry in fetched:
        if entry.code == 'ok':
            for day, close in entry.rows:
                rows.append(ParsedRow(len(rows) + 1, entry.symbol, day, close))
                owner.append(entry.symbol)
    per_symbol = {}
    if rows:
        reply = ingest(rows, dry_run)
        for item in reply['rows']:
            per_symbol.setdefault(owner[item['line'] - 1], []).append(item['outcome'])
    symbols, counts = [], {}
    for entry in fetched:
        outcome, latest = entry.code, None
        if entry.code == 'ok':
            seen = per_symbol.get(entry.symbol, [])
            rejected = [code for code in seen if code not in ACCEPTED]
            if rejected:
                outcome = _REJECT_MAP.get(rejected[0], 'invalid_response') if len(set(rejected)) == 1 \
                    else 'invalid_response'
            else:
                outcome = merge(seen)
            latest = entry.rows[-1]
        symbols.append({'symbol': entry.symbol if isinstance(entry.symbol, str) else None, 'outcome': outcome,
                        'dates': len(entry.rows) if entry.code == 'ok' else 0,
                        'latest_date': latest[0] if latest else None,
                        'latest_close': latest[1] if latest else None})
        counts[outcome] = counts.get(outcome, 0) + 1
    return {'dry_run': dry_run, 'source': SOURCE, 'counts': counts, 'symbols': symbols}
