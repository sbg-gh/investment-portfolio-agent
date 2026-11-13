import { useState, type ChangeEvent, type FormEvent } from 'react';
import type { PriceImportResult, PriceOutcome } from '../api/types';
import { Problem } from '../components/Page';
import { useApp } from '../context';
import { describeError } from '../errors';
import { hrefFor } from '../router';

const TEMPLATE = 'symbol,date,close\n';
// Below the 65536-byte web body cap once JSON-escaped; larger files use the CLI.
const MAX_FILE_BYTES = 60000;

const OUTCOME_LABEL: Record<PriceOutcome, string> = {
  imported: 'Added',
  unchanged: 'Already present',
  corrected: 'Corrected (replaces an earlier value; the old one is kept)',
  conflict: 'Conflict (not written)',
  unknown_symbol: 'Symbol not registered',
  invalid_value: 'Invalid price',
  invalid_date: 'Invalid date',
  future_date: 'Date in the future',
  duplicate_row: 'Duplicate row',
  unsupported_kind: 'Fund NAV not supported',
  invalid_format: 'Unreadable format',
};
const OUTCOME_ORDER = Object.keys(OUTCOME_LABEL) as PriceOutcome[];

function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
    reader.onerror = () => reject(new Error('read failed'));
    reader.readAsText(file, 'utf-8');
  });
}

export function PriceImport() {
  const { api, announce } = useApp();
  const [name, setName] = useState('');
  const [text, setText] = useState<string | null>(null);
  const [result, setResult] = useState<PriceImportResult | null>(null);
  const [validated, setValidated] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const key = text === null ? null : `${name}\n${text}`;
  const ready = text !== null && name.trim() !== '';

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    setProblem(null);
    setResult(null);
    setValidated(null);
    setText(null);
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      setProblem(
        'This file is too large for the web importer (limit about 60 KB, roughly 2,000 rows). ' +
          'Use the import-prices command for larger files.',
      );
      return;
    }
    try {
      setText(await readText(file));
    } catch {
      setProblem('Could not read that file.');
    }
  }

  async function submit(dryRun: boolean) {
    if (text === null) return;
    setBusy(true);
    setProblem(null);
    try {
      const next = await api.importPrices(name.trim(), text, dryRun);
      setResult(next);
      if (dryRun) {
        setValidated(`${name.trim()}\n${text}`);
        announce('Validation finished; nothing saved');
      } else {
        setValidated(null);
        announce('Import finished');
      }
    } catch (error) {
      setResult(null);
      setValidated(null);
      setProblem(describeError(error));
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    setCopied(false);
    try {
      await navigator.clipboard.writeText(TEMPLATE);
      setCopied(true);
    } catch {
      setProblem('Could not copy. Type the header line shown above instead.');
    }
  }

  const written = (result?.counts.imported ?? 0) + (result?.counts.corrected ?? 0);
  // Import is allowed only for exactly the text and name that was just validated.
  const canImport = validated !== null && key !== null && validated === `${name.trim()}\n${text ?? ''}`;

  return (
    <section aria-labelledby="prices-title">
      <h2 id="prices-title">Import prices from CSV</h2>
      <p>
        This app does not verify your data or its license. You are responsible for having the right to use and store
        what you import.
      </p>
      <p>
        Columns <code>symbol</code>, <code>date</code> and <code>close</code> are required (any order); other columns
        are ignored. <code>date</code> is YYYY-MM-DD and means 16:00 New York time that day. <code>close</code> is the
        raw close as a plain decimal. Weekend and holiday dates are accepted as given. Stocks and ETFs only; symbols
        must already be registered. The web limit is about 60 KB (roughly 2,000 rows); use the import-prices command
        for larger files.
      </p>
      <pre>symbol,date,close</pre>
      <button type="button" onClick={() => void copy()}>Copy template</button>
      {copied && <p role="status">Template copied.</p>}
      <Problem message={problem} />
      <form onSubmit={(event: FormEvent) => event.preventDefault()}>
        <label htmlFor="pi-name">Price list name</label>
        <input id="pi-name" value={name} maxLength={64} onChange={(e) => setName(e.target.value)} />
        <label htmlFor="pi-file">CSV file</label>
        <input id="pi-file" type="file" accept=".csv,text/csv" onChange={(e) => void choose(e)} />
        <button type="button" disabled={!ready || busy} onClick={() => void submit(true)}>Validate</button>
        <button type="button" disabled={!canImport || busy} onClick={() => void submit(false)}>Import</button>
      </form>
      {result && (
        <div>
          <p role="status">
            {result.dry_run
              ? `Validation only: nothing has been saved. Price list: ${result.source}.`
              : `Import finished into price list ${result.source}.`}
            {!result.dry_run && written > 0 && (
              <>
                {' '}
                <a href={hrefFor({ name: 'portfolios' })}>Go to the portfolio list</a> and choose {result.source} to value
                a portfolio.
              </>
            )}
          </p>
          <ul>
            {OUTCOME_ORDER.filter((code) => (result.counts[code] ?? 0) > 0).map((code) => (
              <li key={code}>{OUTCOME_LABEL[code]}: {result.counts[code]}</li>
            ))}
          </ul>
          {result.ignored_columns.length > 0 && <p>Ignored columns: {result.ignored_columns.join(', ')}</p>}
          <table>
            <caption>Per-row results</caption>
            <thead><tr><th scope="col">Line</th><th scope="col">Symbol</th><th scope="col">Result</th></tr></thead>
            <tbody>
              {result.rows.map((row) => (
                <tr key={row.line} className={row.outcome === 'corrected' ? 'row-corrected' : undefined}>
                  <td className="num">{row.line}</td>
                  <td>{row.symbol ?? '—'}</td>
                  <td>{row.outcome === 'corrected' ? '↻ ' : ''}{OUTCOME_LABEL[row.outcome]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
