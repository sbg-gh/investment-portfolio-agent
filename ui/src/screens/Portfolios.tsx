import { useEffect, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import type { Portfolio } from '../api/types';
import { Page, Problem } from '../components/Page';
import { useApp } from '../context';
import { describeError } from '../errors';
import { hrefFor } from '../router';

const SAMPLE_NAME = 'Sample portfolio (synthetic)';
export function Portfolios() {
  const { api, session, announce } = useApp();
  const [items, setItems] = useState<Portfolio[] | null>(null);
  const [name, setName] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let live = true;
    api.portfolios().then((rows) => live && setItems(rows), (e: unknown) => live && setProblem(describeError(e)));
    return () => {
      live = false;
    };
  }, [api]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    try {
      const created = await api.createPortfolio(name.trim());
      setItems((current) => [...(current ?? []), created]);
      setName('');
      announce(`Portfolio ${created.name} created`);
    } catch (error) {
      setProblem(describeError(error));
    }
  }

  async function createSample() {
    setProblem(null);
    setMissing(false);
    try {
      const sample = await api.createSamplePortfolio();
      setItems((current) => (current ?? []).some((p) => p.id === sample.id)
        ? current : [...(current ?? []), { id: sample.id, name: sample.name }]);
      announce('Sample portfolio ready');
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.code === 'starter_instruments_missing') {
        setMissing(true);
      } else {
        setProblem(describeError(error));
      }
    }
  }
  const hasSample = (items ?? []).some((p) => p.name === SAMPLE_NAME);
  return (
    <Page title="Portfolios">
      <Problem message={problem} />
      {missing && (
        <p role="alert" className="problem">
          {session.role === 'operator'
            ? <>Register the starter instruments first on the <a href={hrefFor({ name: 'operator' })}>Operator screen</a>.</>
            : 'An operator must register the starter instruments first.'}
        </p>
      )}
      {items === null ? (
        <p>Loading…</p>
      ) : items.length === 0 ? (
        <p>No portfolios yet. Create one below.</p>
      ) : (
        <ul>
          {items.map((p) => (
            <li key={p.id}>
              <a href={hrefFor({ name: 'holdings', id: p.id })}>{p.name}</a>{' '}
              <a href={hrefFor({ name: 'valuation', id: p.id })} aria-label={`Valuation for ${p.name}`}>Valuation</a>
            </li>
          ))}
        </ul>
      )}
      {items !== null && !hasSample && (
        <section aria-labelledby="sample-title">
          <h2 id="sample-title">Sample portfolio</h2>
          <p>
            A synthetic sample: 20 watchlist symbols at a round quantity of 100 each. It is not your real positions.
            No prices are included, so its valuation shows missing prices and no total until an operator enters them.
          </p>
          <button type="button" onClick={() => void createSample()}>Create sample portfolio</button>
        </section>
      )}
      <form onSubmit={create}>
        <h2>New portfolio</h2>
        <label htmlFor="portfolio-name">Portfolio name</label>
        <input id="portfolio-name" value={name} maxLength={100} required onChange={(e) => setName(e.target.value)} />
        <button type="submit">Create portfolio</button>
      </form>
    </Page>
  );
}
