import { useEffect, useId, useState, type FormEvent } from 'react';
import type { Holding, Valuation as Report, ValuationLine } from '../api/types';
import { Page, Problem } from '../components/Page';
import { useApp } from '../context';
import { describeError } from '../errors';
import { formatBps, formatGrouped, ratioBps } from '../money/decimal';
import { hrefFor } from '../router';

const DEFAULT_SOURCE = 'operator-entered';
const WINDOW_CHOICES = [1, 3, 5, 7, 14] as const;
const DEFAULT_WINDOW = 1;
const plural = (days: number) => `${days} ${days === 1 ? 'day' : 'days'}`;
const STATUS_LABEL = { fresh: '✓ Fresh', stale: '⚠ Stale', missing: '✕ Missing' } as const;

function age(observedAt: string, asOf: string): string {
  const seconds = (Date.parse(asOf) - Date.parse(observedAt)) / 1000;
  if (!Number.isFinite(seconds) || seconds < 0) return '';
  const hours = Math.floor(seconds / 3600);
  return hours >= 48 ? `${Math.floor(hours / 24)} d old` : `${hours} h old`;
}

function Allocation({ lines, subtotal, names }: { lines: ValuationLine[]; subtotal: string; names: Map<string, string> }) {
  const priced = lines.filter((l) => l.market_value !== null);
  if (priced.length === 0) return null;
  const rows = priced.map((l) => ({ line: l, bps: ratioBps(l.market_value ?? '0', subtotal) }));
  const ROW = 30;
  return (
    <section aria-labelledby="alloc-heading">
      <h2 id="alloc-heading">Allocation of the priced subtotal</h2>
      <svg role="img" aria-label="Bar chart of each priced holding's share of the priced subtotal; the table below has the same figures"
        viewBox={`0 0 10000 ${rows.length * ROW}`} width="100%" height={rows.length * 20} preserveAspectRatio="none">
        {rows.map(({ line, bps }, i) => (
          <g key={line.security_id}>
            <rect x="0" y={i * ROW} width="10000" height="20" className="bar-track" />
            <rect data-bps={bps.toString()} x="0" y={i * ROW} width={bps.toString()} height="20" className="bar" />
          </g>
        ))}
      </svg>
      <table>
        <caption>Allocation of the priced subtotal</caption>
        <thead><tr><th scope="col">Security</th><th scope="col">Share of priced subtotal</th></tr></thead>
        <tbody>
          {rows.map(({ line, bps }) => (
            <tr key={line.security_id}>
              <td>{names.get(line.security_id) ?? line.security_id}</td>
              <td className="num">{formatBps(bps)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function Valuation({ portfolioId }: { portfolioId: string }) {
  const { api, announce } = useApp();
  const listId = useId();
  const [source, setSource] = useState(DEFAULT_SOURCE);
  const [sources, setSources] = useState<string[]>([]);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [windowDays, setWindowDays] = useState<number>(DEFAULT_WINDOW);
  const [report, setReport] = useState<Report | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    Promise.all([api.holdings(portfolioId), api.sources()]).then(
      ([h, s]) => {
        if (!live) return;
        setHoldings(h);
        setSources(s);
      },
      (e: unknown) => live && setProblem(describeError(e)),
    );
    return () => {
      live = false;
    };
  }, [api, portfolioId]);

  async function run(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      const next = await api.valuation(portfolioId, source.trim(), undefined, windowDays);
      setReport(next);
      announce(next.status === 'complete' ? 'Valuation complete' : 'Valuation loaded; total withheld');
    } catch (error) {
      setReport(null);
      setProblem(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  const names = new Map(holdings.map((h) => [h.security_id, h.symbol ?? h.security_id]));
  const missing = report?.lines.filter((l) => l.status === 'missing').length ?? 0;
  const stale = report?.lines.filter((l) => l.status === 'stale').length ?? 0;
  // The window shown is the one the server reports it used, not the local selection.
  const usedDays = report ? Math.round((report.policy.price_max_age_seconds ?? 0) / 86400) : 0;

  return (
    <Page title="Valuation">
      <p><a href={hrefFor({ name: 'holdings', id: portfolioId })}>Back to holdings</a></p>
      <p className="hint">
        Values come only from observations recorded for the chosen source. A total is shown only when every holding
        has a fresh observation.
      </p>
      <form onSubmit={run}>
        <label htmlFor="source">Observation source</label>
        <input id="source" list={listId} value={source} required onChange={(e) => setSource(e.target.value)} />
        <datalist id={listId}>{sources.map((s) => <option key={s} value={s} />)}</datalist>
        <label htmlFor="window">Accept prices up to</label>
        <select id="window" value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))}>
          {WINDOW_CHOICES.map((d) => <option key={d} value={d}>{plural(d)}</option>)}
        </select>
        <p className="hint">
          A Friday close is about 65 hours old on Monday morning, so choose 3 days or more over a weekend.
        </p>
        <button type="submit" disabled={busy}>Value portfolio</button>
      </form>
      <Problem message={problem} />
      {report && (
        <section aria-labelledby="result-heading">
          <h2 id="result-heading">Result</h2>
          <p data-testid="window">Prices up to {plural(usedDays)} old</p>
          {report.status === 'complete' && report.total !== null ? (
            <p data-testid="total" className="total">
              Total: {report.currency} {formatGrouped(report.total)} <span className="asof">as of {report.market_as_of}</span>
            </p>
          ) : (
            <>
              <p data-testid="total-withheld" className="withheld">
                Total withheld: {missing} missing, {stale} stale. A total is not shown unless every holding has a price within {plural(usedDays)}.
              </p>
              <p data-testid="subtotal">
                Partial subtotal (priced lines only): {report.currency} {formatGrouped(report.priced_subtotal)}{' '}
                <span className="asof">as of {report.market_as_of}</span>
              </p>
            </>
          )}
          <table>
            <caption>Valuation lines</caption>
            <thead>
              <tr>
                <th scope="col">Security</th><th scope="col">Quantity</th><th scope="col">Status</th>
                <th scope="col">Value ({report.currency})</th><th scope="col">Unit value</th><th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {report.lines.map((l) => (
                <tr key={l.security_id}>
                  <td>{names.get(l.security_id) ?? l.security_id}</td>
                  <td className="num">{l.quantity}</td>
                  <td>{STATUS_LABEL[l.status]}</td>
                  <td className="num">
                    {l.market_value === null || l.observed_at === null ? (
                      <>—<br /><small>No observation</small></>
                    ) : (
                      <>
                        {formatGrouped(l.market_value)}<br />
                        <small>observed {l.observed_at} ({age(l.observed_at, report.market_as_of)}), as of {report.market_as_of}</small>
                      </>
                    )}
                  </td>
                  <td className="num">{l.unit_value ?? '—'}</td>
                  <td>{l.source ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Allocation lines={report.lines} subtotal={report.priced_subtotal} names={names} />
        </section>
      )}
    </Page>
  );
}
