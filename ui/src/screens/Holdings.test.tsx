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

  async function importUnknown(api: ReturnType<typeof fakeApi>, role: 'user' | 'operator', typed = 'amd') {
    const user = userEvent.setup();
    renderScreen(<Holdings portfolioId="p-1" />, api, role);
    await screen.findByRole('table', { name: /holdings/i });
    await user.type(screen.getByLabelText(/symbol/i), typed);
    await user.type(screen.getByLabelText(/quantity/i), '3');
    await user.click(screen.getByRole('button', { name: /import holding/i }));
    return await screen.findByRole('alert');
  }
  const UNKNOWN = () => Promise.reject(new ApiError(422, 'invalid_request'));
  it('links an operator to the registration form with the symbol pre-filled', async () => {
    const alert = await importUnknown(fakeApi({ importHolding: UNKNOWN }), 'operator');
    expect(alert.textContent).toMatch(/not registered/i);
    expect(within(alert).getByRole('link', { name: /register amd/i }).getAttribute('href')).toBe(
      '#/operator?symbol=AMD',
    );
  });
  it('tells a non-operator that an operator must register the instrument, with no link', async () => {
    const alert = await importUnknown(fakeApi({ importHolding: UNKNOWN }), 'user');
    expect(alert.textContent).toMatch(/an operator must register this instrument before it can be held/i);
    expect(within(alert).queryByRole('link')).toBeNull();
    expect(alert.textContent).not.toMatch(/amd/i);
  });
  it('does not build a link from an invalid symbol and never shows server text', async () => {
    const api = fakeApi({ importHolding: () => Promise.reject(new ApiError(422, 'invalid_request')) });
    const alert = await importUnknown(api, 'operator', 'a b<');
    expect(within(alert).queryByRole('link')).toBeNull();
    expect(alert.textContent).toMatch(/not registered/i);
  });
  it('notes when the receipts come from the synthetic sample seed', async () => {
    const api = fakeApi({
      holdings: () => Promise.resolve(HOLDINGS),
      receipts: () => Promise.resolve([{ ...RECEIPTS[0]!, source: 'starter-sample-synthetic' }]),
    });
    renderScreen(<Holdings portfolioId="p-1" />, api);
    expect((await screen.findByText(/synthetic sample/i)).textContent).toMatch(/not your real positions/i);
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

  it('reuses the removal idempotency key when a removal is retried after a failure', async () => {
    const user = userEvent.setup();
    let attempt = 0;
    const api = fakeApi({
      holdings: () => Promise.resolve(HOLDINGS),
      removeHolding: () => (attempt++ === 0 ? Promise.reject(new ApiError(0, 'network')) : Promise.resolve()),
    });
    renderScreen(<Holdings portfolioId="p-1" />, api);
    await user.click(await screen.findByRole('button', { name: /remove aaa/i }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: /remove aaa/i }));
    const calls = (api.removeHolding as unknown as { mock: { calls: string[][] } }).mock.calls;
    expect(calls).toHaveLength(2);
    expect(calls[1]?.[2]).toBe(calls[0]?.[2]);
  });

  it('shows a generic not-found for a foreign or missing portfolio', async () => {
    const api = fakeApi({ holdings: () => Promise.reject(new ApiError(404, 'not_found')) });
    renderScreen(<Holdings portfolioId="other" />, api);
    expect((await screen.findByRole('alert')).textContent).toMatch(/not found/i);
  });
});
