import { useEffect, useState, type FormEvent } from 'react';
import type { Portfolio } from '../api/types';
import { Page, Problem } from '../components/Page';
import { useApp } from '../context';
import { describeError } from '../errors';
import { hrefFor } from '../router';

export function Portfolios() {
  const { api, announce } = useApp();
  const [items, setItems] = useState<Portfolio[] | null>(null);
  const [name, setName] = useState('');
  const [problem, setProblem] = useState<string | null>(null);

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

  return (
    <Page title="Portfolios">
      <Problem message={problem} />
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
      <form onSubmit={create}>
        <h2>New portfolio</h2>
        <label htmlFor="portfolio-name">Portfolio name</label>
        <input id="portfolio-name" value={name} maxLength={100} required onChange={(e) => setName(e.target.value)} />
        <button type="submit">Create portfolio</button>
      </form>
    </Page>
  );
}
