import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it, vi } from 'vitest';
import type { Valuation as ValuationReport, ValuationLine } from '../api/types';
import { fakeApi } from '../test/fake-api';
import { renderScreen } from '../test/render';
import { Valuation } from './Valuation';

const AS_OF = '2025-03-01T12:00:00+00:00';
function line(over: Partial<ValuationLine>): ValuationLine {
  return {
    security_id: 'sec-1', kind: 'stock', quantity: '1', basis: 'price', status: 'fresh',
    market_value: '100', unit_value: '100', observation_id: 'o1', source: 'operator-entered',
    observed_at: '2025-03-01T11:00:00+00:00', fetched_at: '2025-03-01T11:00:01+00:00', ...over,
  };
}
function report(over: Partial<ValuationReport>): ValuationReport {
  return {
    snapshot_id: 's', currency: 'USD', market_as_of: AS_OF, source: 'operator-entered',
    policy: { price_max_age_seconds: 86400, nav_max_age_seconds: 259200 },
    status: 'complete', total: '400', priced_subtotal: '400',
    lines: [line({ security_id: 'sec-1', market_value: '100' }), line({ security_id: 'sec-2', market_value: '300' })],
    ...over,
  };
}
const HOLDINGS = [
  { security_id: 'sec-1', symbol: 'AAA', kind: 'stock', quantity: '1' },
  { security_id: 'sec-2', symbol: 'BBB', kind: 'stock', quantity: '3' },
  { security_id: 'sec-3', symbol: 'CCC', kind: 'stock', quantity: '2' },
];

async function run(r: ValuationReport) {
  const user = userEvent.setup();
  const api = fakeApi({
    valuation: () => Promise.resolve(r),
    holdings: () => Promise.resolve(HOLDINGS),
    sources: () => Promise.resolve(['operator-entered']),
  });
  const view = renderScreen(<Valuation portfolioId="p-1" />, api);
  await user.click(await screen.findByRole('button', { name: /value portfolio/i }));
  return { api, ...view };
}

describe('Valuation', () => {
  it('requests the report for the chosen source', async () => {
    const { api } = await run(report({}));
    await screen.findByTestId('total');
    const call = (api.valuation as unknown as { mock: { calls: string[][] } }).mock.calls[0];
    expect(call?.[0]).toBe('p-1');
    expect(call?.[1]).toBe('operator-entered');
  });

  it('shows the total with its as-of time only when the valuation is complete', async () => {
    const { container } = await run(report({}));
    const total = await screen.findByTestId('total');
    expect(total.textContent).toMatch(/USD 400/);
    expect(total.textContent).toMatch(/as of 2025-03-01T12:00:00\+00:00/i);
    expect(await axe(container)).toHaveNoViolations();
  });

  it.each([
    ['incomplete', 'missing'],
    ['stale', 'stale'],
  ] as const)('withholds the total when the status is %s', async (status, lineStatus) => {
    const lines = [
      line({ security_id: 'sec-1', market_value: '100' }),
      lineStatus === 'missing'
        ? line({ security_id: 'sec-3', status: 'missing', market_value: null, unit_value: null, observed_at: null, fetched_at: null, source: null })
        : line({ security_id: 'sec-3', status: 'stale', market_value: '50', observed_at: '2025-01-01T00:00:00+00:00' }),
    ];
    const { container } = await run(report({ status, total: null, priced_subtotal: '150', lines }));
    const banner = await screen.findByTestId('total-withheld');
    expect(banner.textContent).toMatch(/total withheld/i);
    expect(screen.queryByTestId('total')).toBeNull();
    const subtotal = screen.getByTestId('subtotal');
    expect(subtotal.textContent).toMatch(/partial/i);
    expect(subtotal.textContent).toMatch(/USD 150/);
    expect(subtotal.textContent).toMatch(/as of 2025-03-01T12:00:00\+00:00/i);
    expect(container.textContent).not.toMatch(/USD 400/);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('labels each line with a text status, value, source and observed time', async () => {
    const lines = [
      line({ security_id: 'sec-1', market_value: '100' }),
      line({ security_id: 'sec-3', status: 'missing', market_value: null, unit_value: null, observed_at: null, fetched_at: null, source: null }),
    ];
    await run(report({ status: 'incomplete', total: null, priced_subtotal: '100', lines }));
    const table = await screen.findByRole('table', { name: /valuation lines/i });
    const rows = within(table).getAllByRole('row');
    const aaa = rows.find((r) => within(r).queryByText('AAA'));
    const ccc = rows.find((r) => within(r).queryByText('CCC'));
    expect(aaa?.textContent).toMatch(/fresh/i);
    expect(aaa?.textContent).toMatch(/2025-03-01T11:00:00\+00:00/);
    expect(ccc?.textContent).toMatch(/missing/i);
    expect(ccc?.textContent).toMatch(/no observation/i);
  });

  it('draws allocation bars from integer basis points with a table twin', async () => {
    const { container } = await run(report({}));
    await screen.findByTestId('total');
    const bars = Array.from(container.querySelectorAll('svg rect[data-bps]'));
    expect(bars.map((b) => b.getAttribute('width'))).toEqual(['2500', '7500']);
    const twin = screen.getByRole('table', { name: /allocation/i });
    expect(within(twin).getByText('25.00%')).toBeTruthy();
    expect(within(twin).getByText('75.00%')).toBeTruthy();
    expect(container.querySelector('svg')?.getAttribute('role')).toBe('img');
  });

  it('defaults the window to 1 day and always sends it', async () => {
    const { api } = await run(report({}));
    await screen.findByTestId('total');
    expect((screen.getByLabelText(/accept prices up to/i) as HTMLSelectElement).value).toBe('1');
    const call = (api.valuation as unknown as { mock: { calls: unknown[][] } }).mock.calls[0];
    expect(call?.[2]).toBeUndefined();
    expect(call?.[3]).toBe(1);
  });

  it('offers 1, 3, 5, 7 and 14 days and sends the chosen window', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ valuation: () => Promise.resolve(report({})), holdings: () => Promise.resolve(HOLDINGS) });
    renderScreen(<Valuation portfolioId="p-1" />, api);
    const select = await screen.findByLabelText(/accept prices up to/i);
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual(
      ['1 day', '3 days', '5 days', '7 days', '14 days']);
    await user.selectOptions(select, '5');
    await user.click(screen.getByRole('button', { name: /value portfolio/i }));
    await screen.findByTestId('total');
    const call = (api.valuation as unknown as { mock: { calls: unknown[][] } }).mock.calls[0];
    expect(call?.[3]).toBe(5);
  });

  it('shows the window actually used, taken from the response policy', async () => {
    await run(report({ policy: { price_max_age_seconds: 5 * 86400, nav_max_age_seconds: 5 * 86400 } }));
    expect((await screen.findByTestId('window')).textContent).toMatch(/prices up to 5 days old/i);
  });

  it('says 1 day in the singular', async () => {
    await run(report({}));
    expect((await screen.findByTestId('window')).textContent).toMatch(/prices up to 1 day old/i);
  });

  it('mentions the window when the total is withheld', async () => {
    const lines = [line({ security_id: 'sec-1', status: 'stale', observed_at: '2025-01-01T00:00:00+00:00' })];
    await run(report({ status: 'stale', total: null, priced_subtotal: '100', lines,
      policy: { price_max_age_seconds: 3 * 86400, nav_max_age_seconds: 3 * 86400 } }));
    expect((await screen.findByTestId('total-withheld')).textContent).toMatch(/within 3 days/i);
  });

  it('lists a newly imported source in the source suggestions on first use', async () => {
    const api = fakeApi({ sources: () => Promise.resolve(['my-prices']), holdings: () => Promise.resolve(HOLDINGS) });
    const { container } = renderScreen(<Valuation portfolioId="p-1" />, api);
    await screen.findByLabelText(/accept prices up to/i);
    await vi.waitFor(() => expect(container.querySelector('datalist option[value="my-prices"]')).not.toBeNull());
  });
});
