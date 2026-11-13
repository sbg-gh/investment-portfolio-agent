import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import { fakeApi } from '../test/fake-api';
import { renderScreen } from '../test/render';
import { Holdings } from './Holdings';

const HOLDINGS = [{ security_id: 'sec-1', symbol: 'AAA', kind: 'stock', quantity: '10.5' }];
const RECEIPTS = [
  { id: 'r1', security_id: 'sec-1', source: 'web-ui', market_as_of: '2025-01-02T00:00:00+00:00', quantity: '10.5', action: 'upsert' as const },
];
const KEY = /^[A-Za-z0-9_-]{8,128}$/;

describe('Holdings', () => {
  it('shows holdings and the receipts table', async () => {
    const api = fakeApi({ holdings: () => Promise.resolve(HOLDINGS), receipts: () => Promise.resolve(RECEIPTS) });
    const { container } = renderScreen(<Holdings portfolioId="p-1" />, api);
    const table = await screen.findByRole('table', { name: /holdings/i });
    expect(within(table).getByText('AAA')).toBeTruthy();
    expect(within(table).getByText('10.5')).toBeTruthy();
    const receipts = screen.getByRole('table', { name: /receipts/i });
    expect(within(receipts).getByText('upsert')).toBeTruthy();
    expect(await axe(container)).toHaveNoViolations();
  });

  it('uses a fresh idempotency key for every import action', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    renderScreen(<Holdings portfolioId="p-1" />, api);
    await screen.findByRole('table', { name: /holdings/i });
    for (const qty of ['1', '2']) {
      await user.clear(screen.getByLabelText(/symbol/i));
      await user.type(screen.getByLabelText(/symbol/i), 'AAA');
      await user.clear(screen.getByLabelText(/quantity/i));
      await user.type(screen.getByLabelText(/quantity/i), qty);
      await user.click(screen.getByRole('button', { name: /import holding/i }));
    }
    const calls = (api.importHolding as unknown as { mock: { calls: string[][] } }).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[0]?.[3]).toMatch(KEY);
    expect(calls[1]?.[3]).toMatch(KEY);
    expect(calls[0]?.[3]).not.toBe(calls[1]?.[3]);
    expect(calls[0]?.[2]).toBe('1');
  });

  it('reuses the idempotency key when the same import is retried after a failure', async () => {
    const user = userEvent.setup();
    let attempt = 0;
    const api = fakeApi({
      importHolding: () => (attempt++ === 0 ? Promise.reject(new ApiError(0, 'network')) : Promise.resolve()),
    });
    renderScreen(<Holdings portfolioId="p-1" />, api);
    await screen.findByRole('table', { name: /holdings/i });
    await user.type(screen.getByLabelText(/symbol/i), 'AAA');
    await user.type(screen.getByLabelText(/quantity/i), '1');
    await user.click(screen.getByRole('button', { name: /import holding/i }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: /import holding/i }));
    const calls = (api.importHolding as unknown as { mock: { calls: string[][] } }).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[1]?.[3]).toBe(calls[0]?.[3]);
  });

  it('blocks a non-decimal quantity before calling the API', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    renderScreen(<Holdings portfolioId="p-1" />, api);
    await screen.findByRole('table', { name: /holdings/i });
    await user.type(screen.getByLabelText(/symbol/i), 'AAA');
    await user.type(screen.getByLabelText(/quantity/i), '1e3');
    await user.click(screen.getByRole('button', { name: /import holding/i }));
    expect(api.importHolding).not.toHaveBeenCalled();
    expect((await screen.findByRole('alert')).textContent).toMatch(/plain decimal/i);
  });

  it('explains that an unknown symbol needs operator registration', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ importHolding: () => Promise.reject(new ApiError(422, 'invalid_request')) });
    renderScreen(<Holdings portfolioId="p-1" />, api);
    await screen.findByRole('table', { name: /holdings/i });
    await user.type(screen.getByLabelText(/symbol/i), 'ZZZ');
    await user.type(screen.getByLabelText(/quantity/i), '3');
    await user.click(screen.getByRole('button', { name: /import holding/i }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/operator registration or reconciliation/i);
  });

  it('removes a holding with its own idempotency key and reloads', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ holdings: () => Promise.resolve(HOLDINGS) });
    renderScreen(<Holdings portfolioId="p-1" />, api);
    await user.click(await screen.findByRole('button', { name: /remove aaa/i }));
    const call = (api.removeHolding as unknown as { mock: { calls: string[][] } }).mock.calls[0];
    expect(call?.[0]).toBe('p-1');
    expect(call?.[1]).toBe('sec-1');
    expect(call?.[2]).toMatch(KEY);
  });

  it('shows a generic not-found for a foreign or missing portfolio', async () => {
    const api = fakeApi({ holdings: () => Promise.reject(new ApiError(404, 'not_found')) });
    renderScreen(<Holdings portfolioId="other" />, api);
    expect((await screen.findByRole('alert')).textContent).toMatch(/not found/i);
  });
});
