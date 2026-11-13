import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import type { Holding, Receipt } from '../api/types';
import { Page, Problem } from '../components/Page';
import { useApp } from '../context';
import { DECIMAL_HELP, describeError } from '../errors';
import { SYMBOL, hrefFor } from '../router';
import { isDecimalString } from '../money/decimal';

const SAMPLE_SOURCE = 'starter-sample-synthetic';

export function Holdings({ portfolioId }: { portfolioId: string }) {
  const { api, session, announce } = useApp();
  const [holdings, setHoldings] = useState<Holding[] | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [symbol, setSymbol] = useState('');
  const [quantity, setQuantity] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  // Set when an import was refused because the instrument is not registered. Holds the typed
  // symbol (upper-cased) only if it has a valid shape, so a link is never built from raw input.
  const [unregistered, setUnregistered] = useState<{ symbol: string | null } | null>(null);
  // A key is kept until its action succeeds, so retrying the same action after a lost response
  // reuses it and the server can dedupe. A different action (other symbol/quantity) gets a new key.
  const pending = useRef<{ action: string; key: string } | null>(null);
  function keyFor(action: string): string {
    if (pending.current?.action !== action) pending.current = { action, key: crypto.randomUUID() };
    return pending.current.key;
  }

  const load = useCallback(async () => {
    try {
      const [h, r] = await Promise.all([api.holdings(portfolioId), api.receipts(portfolioId)]);
      setHoldings(h);
      setReceipts(r);
    } catch (error) {
      setProblem(describeError(error));
    }
  }, [api, portfolioId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function importHolding(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    setUnregistered(null);
    if (!isDecimalString(quantity)) {
      setProblem(DECIMAL_HELP);
      return;
    }
    try {
      await api.importHolding(portfolioId, symbol.trim(), quantity, keyFor(`import:${symbol.trim()}:${quantity}`));
      pending.current = null;
      setSymbol('');
      setQuantity('');
      announce('Holding imported');
      await load();
    } catch (error) {
      if (error instanceof ApiError && error.status === 422 && error.code === 'invalid_request') {
        const typed = symbol.trim().toUpperCase();
        setUnregistered({ symbol: SYMBOL.test(typed) ? typed : null });
      } else {
        setProblem(describeError(error));
      }
    }
  }

  async function remove(holding: Holding) {
    setProblem(null);
    try {
      await api.removeHolding(portfolioId, holding.security_id, keyFor(`remove:${holding.security_id}`));
      pending.current = null;
      announce(`${holding.symbol ?? holding.security_id} removed`);
      await load();
    } catch (error) {
      setProblem(describeError(error));
    }
  }

  const names = new Map((holdings ?? []).map((h) => [h.security_id, h.symbol ?? h.security_id]));

  return (
    <Page title="Holdings">
      <p>
        <a href={hrefFor({ name: 'portfolios' })}>All portfolios</a>{' · '}
        <a href={hrefFor({ name: 'valuation', id: portfolioId })}>Valuation</a>
      </p>
      <Problem message={problem} />
      {unregistered && (
        <p role="alert" className="problem">
          {session.role === 'operator'
            ? <>That instrument is not registered, so it cannot be held yet.{' '}
                {unregistered.symbol && (
                  <a href={hrefFor({ name: 'operator', symbol: unregistered.symbol })}>
                    Register {unregistered.symbol} on the Operator screen
                  </a>
                )}</>
            : 'An operator must register this instrument before it can be held.'}
        </p>
      )}
      {receipts.some((r) => r.source === SAMPLE_SOURCE) && (
        <p>This portfolio was created from the synthetic sample. It is not your real positions.</p>
      )}
      {holdings === null ? (
        problem ? null : <p>Loading…</p>
      ) : (
        <table>
          <caption>Holdings</caption>
          <thead><tr><th scope="col">Symbol</th><th scope="col">Kind</th><th scope="col">Quantity</th><th scope="col">Actions</th></tr></thead>
          <tbody>
            {holdings.length === 0 ? (
              <tr><td colSpan={4}>No holdings yet.</td></tr>
            ) : holdings.map((h) => (
              <tr key={h.security_id}>
                <td>{h.symbol ?? h.security_id}</td>
                <td>{h.kind ?? 'unknown'}</td>
                <td className="num">{h.quantity}</td>
                <td>
                  <button type="button" aria-label={`Remove ${h.symbol ?? h.security_id}`} onClick={() => void remove(h)}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form onSubmit={importHolding}>
        <h2>Import a holding</h2>
        <label htmlFor="symbol">Symbol</label>
        <input id="symbol" value={symbol} maxLength={15} required onChange={(e) => setSymbol(e.target.value)} />
        <label htmlFor="quantity">Quantity</label>
        <input id="quantity" value={quantity} inputMode="decimal" required aria-describedby="quantity-help"
          onChange={(e) => setQuantity(e.target.value)} />
        <p id="quantity-help" className="hint">{DECIMAL_HELP}</p>
        <button type="submit">Import holding</button>
      </form>
      {holdings === null ? null : (
        <table>
          <caption>Receipts</caption>
          <thead><tr><th scope="col">Holding</th><th scope="col">Action</th><th scope="col">Quantity</th><th scope="col">Source</th><th scope="col">Market as of</th></tr></thead>
          <tbody>
            {receipts.length === 0 ? (
              <tr><td colSpan={5}>No receipts yet.</td></tr>
            ) : receipts.map((r) => (
              <tr key={r.id}>
                <td>{names.get(r.security_id) ?? r.security_id}</td>
                <td>{r.action}</td>
                <td className="num">{r.quantity}</td>
                <td>{r.source}</td>
                <td>{r.market_as_of}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Page>
  );
}
