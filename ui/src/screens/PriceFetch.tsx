import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import type { FetchOutcome, PriceFetchResult, PriceFetchStatus } from '../api/types';
import { Problem } from '../components/Page';
import { useApp } from '../context';
import { describeError } from '../errors';
import { hrefFor } from '../router';
const OUTCOME_LABEL: Record<FetchOutcome, string> = {
  imported: 'Added',
  unchanged: 'Already up to date',
  corrected: 'Corrected (replaces an earlier value; the old one is kept)',
  not_found: 'Not found at Yahoo',
  unsupported_kind: 'Not a stock or ETF',
  rate_limited: 'Stopped: Yahoo is rate limiting requests',
  blocked: 'Stopped: Yahoo blocked the request',
  provider_error: 'Yahoo error (try again later)',
  invalid_response: 'Unusable response (nothing saved)',
  skipped_budget: 'Skipped: request limit for one press reached',
  skipped_stopped: 'Skipped: the press was stopped',
};
const OUTCOME_ORDER = Object.keys(OUTCOME_LABEL) as FetchOutcome[];
const ACCEPTED: FetchOutcome[] = ['imported', 'unchanged', 'corrected'];
const FAILURE_TEXT: Record<string, string> = {
  forbidden: 'Your operator credential is no longer valid, so the fetch was cancelled and nothing was saved.',
  interrupted: 'The fetch was interrupted (the server was stopping) and nothing was saved.',
};
function failureText(code: string | null | undefined): string {
  return (code && FAILURE_TEXT[code]) ?? 'The fetch failed and nothing was saved. Please try again.';
}
export function PriceFetch({ pollMs = 1500 }: { pollMs?: number }) {
  const { api, announce } = useApp();
  const [dryRun, setDryRun] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{ done: number; planned: number } | null>(null);
  const [result, setResult] = useState<PriceFetchResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current !== null) clearTimeout(timer.current);
    };
  }, []);
  function stop(message: string | null) {
    setRunning(false);
    setProgress(null);
    if (message) setProblem(message);
  }
  async function poll(fetchId: string) {
    if (!alive.current) return;
    let status: PriceFetchStatus;
    try {
      status = await api.priceFetchStatus(fetchId);
    } catch (error) {
      if (alive.current) stop(describeError(error));
      return;
    }
    if (!alive.current) return;
    if (status.state === 'running') {
      if (status.progress) setProgress(status.progress);
      timer.current = setTimeout(() => void poll(fetchId), pollMs);
    } else if (status.state === 'done' && status.result) {
      setResult(status.result);
      stop(null);
      announce('Price fetch finished');
    } else if (status.state === 'failed') {
      stop(failureText(status.error));
    } else {
      // `unknown`: the server no longer has this fetch (restart or replaced). Nothing partial is written.
      stop('This fetch was interrupted: the server no longer has a record of it (it may have restarted). ' +
        'Nothing partial was saved. Press the button again to retry.');
    }
  }
  async function start() {
    setProblem(null);
    setResult(null);
    setProgress(null);
    setRunning(true);
    try {
      const { fetch_id } = await api.startPriceFetch(dryRun);
      if (alive.current) timer.current = setTimeout(() => void poll(fetch_id), pollMs);
    } catch (error) {
      stop(error instanceof ApiError && error.status === 409
        ? 'A fetch is already running. Wait for it to finish, then try again.'
        : describeError(error));
    }
  }
  const failed = result ? result.symbols.filter((row) => !ACCEPTED.includes(row.outcome)) : [];
  const stoppedEarly = result ? (result.counts.blocked ?? 0) + (result.counts.rate_limited ?? 0) > 0 : false;
  return (
    <section aria-labelledby="fetch-title">
      <h2 id="fetch-title">Fetch prices from Yahoo (unofficial)</h2>
      <p>
        This reads end-of-day closes from Yahoo Finance&apos;s unofficial chart endpoint for the registered stocks and
        ETFs. It is unofficial and can break or be blocked at any time. Automated retrieval is outside Yahoo&apos;s terms
        and was accepted by the operator. Prices are end-of-day closes of finished sessions, shown with their dates. They
        are representative and not authoritative. Only ticker symbols and a fixed 5-day range are sent to Yahoo; a press
        takes about 30 to 60 seconds and nothing runs on a schedule.
      </p>
      <Problem message={problem} />
      <label htmlFor="fetch-dry">
        <input id="fetch-dry" type="checkbox" checked={dryRun} disabled={running}
          onChange={(event) => setDryRun(event.target.checked)} />{' '}
        Dry run (fetch and check, save nothing)
      </label>
      <button type="button" disabled={running} onClick={() => void start()}>
        Fetch prices from Yahoo (unofficial)
      </button>
      {running && (
        <div role="progressbar" aria-label="Fetch progress" aria-valuemin={0} aria-valuemax={progress?.planned ?? 0}
          aria-valuenow={progress?.done ?? 0}>
          {progress ? `Fetching: ${progress.done} of ${progress.planned}` : 'Starting the fetch'}
        </div>
      )}
      {result && (
        <div>
          <p role="status">
            {result.dry_run ? 'Dry run: nothing was saved. ' : ''}
            {failed.length === 0
              ? `All ${result.symbols.length} symbols are up to date.`
              : `${failed.length} of ${result.symbols.length} symbols were not updated.`}
            {!result.dry_run && result.symbols.length > failed.length && (
              <>
                {' '}
                <a href={hrefFor({ name: 'portfolios' })}>Go to the portfolio list</a> and choose {result.source} to value
                a portfolio.
              </>
            )}
          </p>
          {stoppedEarly && (
            <p role="alert">The press stopped early because Yahoo refused further requests. Nothing was retried.</p>
          )}
          <ul>
            {OUTCOME_ORDER.filter((code) => (result.counts[code] ?? 0) > 0).map((code) => (
              <li key={code}>{OUTCOME_LABEL[code]}: {result.counts[code]}</li>
            ))}
          </ul>
          <table>
            <caption>Per-symbol results</caption>
            <thead>
              <tr>
                <th scope="col">Symbol</th><th scope="col">Result</th>
                <th scope="col">Latest close (USD)</th><th scope="col">Close date</th>
              </tr>
            </thead>
            <tbody>
              {result.symbols.map((row, index) => (
                <tr key={`${row.symbol ?? ''}-${index}`}
                  className={ACCEPTED.includes(row.outcome) ? undefined : 'row-failed'}>
                  <td>{row.symbol ?? '—'}</td>
                  <td>{ACCEPTED.includes(row.outcome) ? '' : '⚠ '}{OUTCOME_LABEL[row.outcome]}</td>
                  <td className="num">{row.latest_close ?? '—'}</td>
                  <td>{row.latest_date ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.omitted > 0 && <p>{result.omitted} more symbols are not listed here.</p>}
        </div>
      )}
    </section>
  );
}
