import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import type { PriceImportResult } from '../api/types';
import { fakeApi } from '../test/fake-api';
import { renderScreen } from '../test/render';
import { Operator } from './Operator';
import { PriceImport } from './PriceImport';

const CSV = 'symbol,date,close\nSYNA,2020-01-02,1.5\n';

function result(over: Partial<PriceImportResult> = {}): PriceImportResult {
  return {
    dry_run: true, source: 'my-prices', total_rows: 3, counts: { imported: 1, corrected: 1, unknown_symbol: 1 },
    ignored_columns: ['volume'],
    rows: [
      { line: 2, symbol: 'SYNA', outcome: 'imported' },
      { line: 3, symbol: 'SYNB', outcome: 'corrected' },
      { line: 4, symbol: 'NOPE', outcome: 'unknown_symbol' },
    ],
    ...over,
  };
}

async function setup(api = fakeApi({ importPrices: (_s: string, _t: string, dry: boolean) => Promise.resolve(result({ dry_run: dry })) })) {
  const user = userEvent.setup();
  const view = renderScreen(<PriceImport />, api, 'operator');
  return { user, api, ...view };
}

async function fill(user: ReturnType<typeof userEvent.setup>, text = CSV, name = 'my-prices') {
  await user.clear(screen.getByLabelText(/price list name/i));
  await user.type(screen.getByLabelText(/price list name/i), name);
  const file = new File([text], 'prices.csv', { type: 'text/csv' });
  await user.upload(screen.getByLabelText(/csv file/i), file);
}

const importButton = () => screen.getByRole('button', { name: /^import$/i }) as HTMLButtonElement;
const validateButton = () => screen.getByRole('button', { name: /^validate$/i }) as HTMLButtonElement;

describe('PriceImport', () => {
  it('is part of the operator page', async () => {
    renderScreen(<Operator />, fakeApi(), 'operator');
    expect(await screen.findByRole('heading', { name: /import prices from csv/i })).toBeTruthy();
  });

  it('states the license responsibility, the format, the limits and offers no real tickers', async () => {
    const { container } = await setup();
    expect(screen.getByText(/does not verify your data or its license/i).textContent).toMatch(
      /responsible for having the right to use and store what you import/i);
    expect(container.textContent).toMatch(/symbol,date,close/);
    expect(container.textContent).toMatch(/raw close/i);
    expect(container.textContent).toMatch(/weekend/i);
    expect(container.textContent).toMatch(/import-prices/);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('copies exactly the header-only template', async () => {
    const { user } = await setup();
    await user.click(screen.getByRole('button', { name: /copy template/i }));
    expect(await navigator.clipboard.readText()).toBe('symbol,date,close\n');
  });

  it('validates with dry_run true and shows counts, source, ignored columns and the row table', async () => {
    const { user, api, container } = await setup();
    await fill(user);
    await user.click(validateButton());
    expect(api.importPrices).toHaveBeenCalledWith('my-prices', CSV, true);
    const table = await screen.findByRole('table', { name: /per-row results/i });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(4);
    expect(within(table).getByText('SYNA')).toBeTruthy();
    expect(container.textContent).toMatch(/my-prices/);
    expect(container.textContent).toMatch(/volume/);
    expect(screen.getByText(/added/i, { selector: 'li' }).textContent).toMatch(/1/);
    expect(screen.getByText(/validation only/i)).toBeTruthy();
    expect(await axe(container)).toHaveNoViolations();
  });

  it('marks corrected rows with text and a class, not colour alone', async () => {
    const { user } = await setup();
    await fill(user);
    await user.click(validateButton());
    const row = (await screen.findByText('SYNB')).closest('tr');
    expect(row?.className).toMatch(/corrected/);
    expect(row?.textContent).toMatch(/↻/);
    expect(row?.textContent).toMatch(/replaces an earlier value; the old one is kept/i);
  });

  it('keeps Import disabled until the identical text has been validated, then sends dry_run false', async () => {
    const { user, api } = await setup();
    await fill(user);
    expect(importButton().disabled).toBe(true);
    await user.click(validateButton());
    await screen.findByRole('table', { name: /per-row results/i });
    expect(importButton().disabled).toBe(false);
    await user.click(importButton());
    expect(api.importPrices).toHaveBeenLastCalledWith('my-prices', CSV, false);
    expect((await screen.findByText(/import finished/i)).textContent).toMatch(/my-prices/);
    expect(screen.getByRole('link', { name: /portfolio list/i }).getAttribute('href')).toBe('#/portfolios');
  });

  it('disables Import again after the name or the file changes', async () => {
    const { user } = await setup();
    await fill(user);
    await user.click(validateButton());
    await screen.findByRole('table', { name: /per-row results/i });
    await user.type(screen.getByLabelText(/price list name/i), 'x');
    expect(importButton().disabled).toBe(true);
    await user.click(validateButton());
    await screen.findByRole('table', { name: /per-row results/i });
    expect(importButton().disabled).toBe(false);
    await user.upload(screen.getByLabelText(/csv file/i), new File([CSV + 'SYNB,2020-01-02,2\n'], 'b.csv'));
    expect(importButton().disabled).toBe(true);
  });

  it('refuses an oversized file without sending anything', async () => {
    const { user, api } = await setup();
    await fill(user, 'x'.repeat(60001));
    expect((await screen.findByRole('alert')).textContent).toMatch(/too large for the web importer/i);
    expect(validateButton().disabled).toBe(true);
    expect(api.importPrices).not.toHaveBeenCalled();
  });

  it('renders hostile names as text, never as markup', async () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const api = fakeApi({
      importPrices: () => Promise.resolve(result({ ignored_columns: [hostile], rows: [{ line: 2, symbol: hostile, outcome: 'imported' }] })),
    });
    const { user, container } = await setup(api);
    await fill(user);
    await user.click(validateButton());
    await screen.findByRole('table', { name: /per-row results/i });
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain(hostile);
  });

  it('shows a plain sentence for a file-level error and no server text', async () => {
    const api = fakeApi({ importPrices: () => Promise.reject(new ApiError(422, 'invalid_header')) });
    const { user } = await setup(api);
    await fill(user);
    await user.click(validateButton());
    expect((await screen.findByRole('alert')).textContent).toMatch(/symbol, date and close/i);
    expect(importButton().disabled).toBe(true);
  });

  it('shows the operator-role message to a non-operator on the operator page', async () => {
    renderScreen(<Operator />, fakeApi(), 'user');
    expect((await screen.findByRole('alert')).textContent).toMatch(/operator role/i);
    expect(screen.queryByRole('button', { name: /^validate$/i })).toBeNull();
  });
});
