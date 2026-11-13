"""Yahoo chart response fixtures for offline tests (NOT packaged, never fetched at test time).

AMD_RECORDED and NOT_FOUND_404 are taken from the research report
(.agents/tasks/eod-price-source-report.md): recorded 2026-11-13 from a public endpoint, trimmed,
the in-progress 11/13 bar removed and floats rounded to 4 dp. Everything built by `chart()` is a
constructed edge case, not a recording.
"""
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo
AMD_RECORDED = (
    '{"chart":{"result":[{"meta":{"currency":"USD","symbol":"AMD","exchangeName":"NMS",'
    '"instrumentType":"EQUITY","regularMarketTime":1794585087,"gmtoffset":-18000,'
    '"exchangeTimezoneName":"America/New_York","dataGranularity":"1d","range":"5d"},'
    '"timestamp":[1794321000,1794407400,1794493800],"indicators":{"quote":[{"open":[502.8,543.08,569.28],'
    '"high":[539.25,567.65,594.0],"low":[495.0,522.65,554.9],"close":[515.21,552.9,585.3],'
    '"volume":[47159100,45872800,55161000]}],"adjclose":[{"adjclose":[515.21,552.9,585.3]}]}}],"error":null}}')
NOT_FOUND_404 = ('{"chart":{"result":null,"error":{"code":"Not Found",'
                 '"description":"No data found, symbol may be delisted"}}}')
HTML_CHALLENGE = b'<!doctype html><html><body>Verify you are human</body></html>'
def ts(year, month, day, hour=9, minute=30, tz='America/New_York'):
    return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc).timestamp())
def chart(symbol='SYNA', bars=(), *, currency='USD', kind='EQUITY', tz='America/New_York',
          end=None, extra_meta=None, raw=None):
    """bars: iterable of (epoch_seconds, close). close may be None, a float or a raw JSON-able value."""
    meta = {'currency': currency, 'symbol': symbol, 'instrumentType': kind,
            'exchangeTimezoneName': tz, 'dataGranularity': '1d', 'range': '5d'}
    if end is not None:
        meta['currentTradingPeriod'] = {'regular': {'start': end - 23400, 'end': end}}
    meta.update(extra_meta or {})
    bars = list(bars)
    body = {'chart': {'result': [{'meta': meta, 'timestamp': [b[0] for b in bars],
                                  'indicators': {'quote': [{'close': [b[1] for b in bars]}],
                                                 'adjclose': [{'adjclose': [b[1] for b in bars]}]}}],
                      'error': None}}
    if raw is not None:
        return raw(body)
    return json.dumps(body).encode()
