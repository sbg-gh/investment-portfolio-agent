import { useState, type FormEvent } from 'react';
import type { StarterInstrumentsResult } from '../api/types';
import { Page, Problem } from '../components/Page';
import { useApp } from '../context';
import { DECIMAL_HELP, describeError } from '../errors';
import { isDecimalString } from '../money/decimal';
import { hrefFor } from '../router';

/** datetime-local gives minutes (or seconds) with no zone; this form is explicitly UTC. */
function utcStamp(local: string): string {
  const withSeconds = local.length === 16 ? `${local}:00` : local;
  return `${withSeconds}+00:00`;
}

export function Operator({ symbol }: { symbol?: string | undefined }) {
  const { api, session, announce } = useApp();
  // A symbol from the route (already validated by the router) only pre-fills the form.
  const [regId, setRegId] = useState(symbol ? `op:${symbol.toLowerCase()}` : '');
  const [regSymbol, setRegSymbol] = useState(symbol ?? '');
  const [starter, setStarter] = useState<StarterInstrumentsResult | null>(null);
  const [regKind, setRegKind] = useState('stock');
  const [regDone, setRegDone] = useState<string | null>(null);
  const [obsId, setObsId] = useState('');
  const [value, setValue] = useState('');
  const [basis, setBasis] = useState<'price' | 'nav'>('price');
  const [source, setSource] = useState('operator-entered');
  const [observedAt, setObservedAt] = useState('');
  const [obsDone, setObsDone] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  if (session.role !== 'operator') {
    return (
      <Page title="Operator">
        <Problem message="The operator role is required to use this page." />
      </Page>
    );
  }

  async function register(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    setRegDone(null);
    try {
      const id = await api.registerInstrument(regId.trim(), regSymbol.trim().toUpperCase(), regKind);
      setRegDone(`Registered ${id.security_id}.`);
      announce('Instrument registered');
    } catch (error) {
      setProblem(describeError(error));
    }
  }

  async function registerStarter() {
    setProblem(null);
    setStarter(null);
    try {
      setStarter(await api.registerStarterInstruments());
      announce('Starter instruments registered');
    } catch (error) {
      setProblem(describeError(error));
    }
  }
  async function observe(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    setObsDone(null);
    if (!isDecimalString(value)) {
      setProblem(DECIMAL_HELP);
      return;
    }
    try {
      await api.addObservation({
        security_id: obsId.trim(), value, basis, source: source.trim(), observed_at: utcStamp(observedAt),
      });
      setObsDone('Observation recorded.');
      announce('Observation recorded');
    } catch (error) {
      setProblem(describeError(error));
    }
  }

  return (
    <Page title="Operator">
      <p>
        There is no live market data. Prices and NAVs are entered here by hand, and instruments are operator-attested:
        nothing checks them against a market-data authority.
      </p>
      <Problem message={problem} />
      <section aria-labelledby="starter-title">
        <h2 id="starter-title">Starter instruments</h2>
        <p>
          Registers the 20 symbols of the AI Conviction watchlist as US stocks, attested by you as operator. SKHY is the
          user&apos;s own attestation that it is a US-listed stock; it is not verified by any authority. No prices
          are included: enter them in the observation form below.
        </p>
        <button type="button" onClick={() => void registerStarter()}>Register starter instruments</button>
        {starter && (
          <p role="status">
            {starter.registered} registered, {starter.already_present} already present, of {starter.symbols}.{' '}
            <a href={hrefFor({ name: 'portfolios' })}>Go to portfolios</a> to create the sample portfolio.
          </p>
        )}
      </section>
      <form onSubmit={register}>
        <h2>Register instrument</h2>
        <label htmlFor="reg-id">Security ID</label>
        <input id="reg-id" value={regId} required maxLength={64} onChange={(e) => setRegId(e.target.value)} />
        <label htmlFor="reg-symbol">Symbol</label>
        <input id="reg-symbol" value={regSymbol} required maxLength={15} onChange={(e) => setRegSymbol(e.target.value)} />
        <label htmlFor="reg-kind">Kind</label>
        <select id="reg-kind" value={regKind} onChange={(e) => setRegKind(e.target.value)}>
          <option value="stock">stock</option>
          <option value="etf">etf</option>
          <option value="mutual_fund">mutual_fund</option>
        </select>
        <button type="submit">Register instrument</button>
        {regDone && <p role="status">{regDone}</p>}
      </form>
      <form onSubmit={observe}>
        <h2>Record observation</h2>
        <label htmlFor="obs-id">Security ID</label>
        <input id="obs-id" value={obsId} required maxLength={64} onChange={(e) => setObsId(e.target.value)} />
        <label htmlFor="obs-value">Value (USD)</label>
        <input id="obs-value" value={value} inputMode="decimal" required onChange={(e) => setValue(e.target.value)} />
        <label htmlFor="obs-basis">Basis</label>
        <select id="obs-basis" value={basis} onChange={(e) => setBasis(e.target.value as 'price' | 'nav')}>
          <option value="price">price</option>
          <option value="nav">nav</option>
        </select>
        <label htmlFor="obs-source">Source</label>
        <input id="obs-source" value={source} required maxLength={64} onChange={(e) => setSource(e.target.value)} />
        <label htmlFor="obs-at">Observed at (UTC)</label>
        <input id="obs-at" type="datetime-local" value={observedAt} required onChange={(e) => setObservedAt(e.target.value)} />
        <button type="submit">Record observation</button>
        {obsDone && <p role="status">{obsDone}</p>}
      </form>
    </Page>
  );
}
