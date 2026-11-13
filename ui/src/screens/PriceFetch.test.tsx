import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import type { PriceFetchResult, PriceFetchStatus } from '../api/types';
import { fakeApi } from '../test/fake-api';
import { renderScreen } from '../test/render';
import { Operator } from './Operator';
import { PriceFetch } from './PriceFetch';
function result(over: Partial<PriceFetchResult> = {}): PriceFetchResult {
  return {
    dry_run: false, source: 'yahoo-chart', counts: { imported: 1, unchanged: 1, not_found: 1 }, omitted: 0,
    symbols: [
      { symbol: 'SYNA', outcome: 'imported', dates: 3, latest_date: '2026-11-12', latest_close: '585.3000' },
      { symbol: 'SYNB', outcome: 'unchanged', dates: 3, latest_date: '2026-11-12', latest_close: '12.5000' },
      { symbol: 'SYNC', outcome: 'not_found', dates: 0, latest_date: null, latest_close: null },
    ],
    ...over,
  };
}
const done = (over: Partial<PriceFetchResult> = {}): PriceFetchStatus => ({
  state: 'done', fetch_id: 'f1', dry_run: false, progress: { done: 3, planned: 3 }, result: result(over), error: null,
});
const running = (n: number, of = 3): PriceFetchStatus => ({
  state: 'running', fetch_id: 'f1', dry_run: false, progress: { done: n, planned: of }, result: null, error: null,
});
function scripted(statuses: PriceFetchStatus[]) {
  let i = 0;
  return () => Promise.resolve(statuses[Math.min(i++, statuses.length - 1)]);
}
function setup(statuses: PriceFetchStatus[], overrides: Record<string, unknown> = {}) {
  const api = fakeApi({
    startPriceFetch: () => Promise.resolve({ fetch_id: 'f1' }),
    priceFetchStatus: scripted(statuses),
    ...overrides,
  });
  const user = userEvent.setup();
  const view = renderScreen(<PriceFetch pollMs={5} />, api, 'operator');
  return { api, user, ...view };
}
const button = () => screen.getByRole('button', { name: /fetch prices from yahoo \(unofficial\)/i }) as HTMLButtonElement;
describe('PriceFetch', () => {
  it('is part of the operator page', async () => {
    renderScreen(<Operator />, fakeApi(), 'operator');
    expect(await screen.findByRole('heading', { name: /fetch prices from yahoo/i })).toBeTruthy();
  });
  it('states honestly what this is and what leaves the machine', async () => {
    const { container } = setup([done()]);
    const text = container.textContent ?? '';
    expect(text).toMatch(/unofficial/i);
    expect(text).toMatch(/outside Yahoo.s terms/i);
    expect(text).toMatch(/accepted by the operator/i);
    expect(text).toMatch(/end-of-day closes/i);
    expect(text).toMatch(/dates/i);
    expect(text).toMatch(/representative/i);
    expect(text).toMatch(/not authoritative/i);
    expect(text).toMatch(/ticker symbols/i);
    expect(await axe(container)).toHaveNoViolations();
  });
  it('starts a real press, shows progress, disables the button while running, then the per-symbol results', async () => {
    const { user, api, container } = setup([running(1), running(2), done()]);
    await user.click(button());
    expect(api.startPriceFetch).toHaveBeenCalledWith(false);
    await waitFor(() => expect(button().disabled).toBe(true));
    expect((await screen.findByRole('progressbar')).getAttribute('aria-valuenow')).toMatch(/^[123]$/);
    expect(container.textContent).toMatch(/Fetching: \d of 3/);
    const table = await screen.findByRole('table', { name: /per-symbol results/i });
    expect(within(table).getAllByRole('row')).toHaveLength(4);
    expect(within(table).getByText('585.3000')).toBeTruthy();
    expect(within(table).getAllByText('2026-11-12').length).toBe(2);
    expect(screen.getByText(/added/i, { selector: 'li' }).textContent).toMatch(/1/);
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(button().disabled).toBe(false);
    expect(await axe(container)).toHaveNoViolations();
  });
  it('after a real press, points to valuation with the yahoo-chart source (first-use path)', async () => {
    const { user, container } = setup([done()]);
    await user.click(button());
    await screen.findByRole('table', { name: /per-symbol results/i });
    expect(container.textContent).toMatch(/choose yahoo-chart/i);
    expect(screen.getByRole('link', { name: /portfolio list/i }).getAttribute('href')).toBe('#/portfolios');
  });
  it('a dry run sends dry_run true and says nothing was saved', async () => {
    const { user, api, container } = setup([done({ dry_run: true })]);
    await user.click(screen.getByLabelText(/dry run/i));
    await user.click(button());
    expect(api.startPriceFetch).toHaveBeenCalledWith(true);
    await screen.findByRole('table', { name: /per-symbol results/i });
    expect(container.textContent).toMatch(/dry run: nothing was saved/i);
    expect(screen.queryByRole('link', { name: /portfolio list/i })).toBeNull();
  });
  it('shows failed and skipped symbols clearly in plain language with a count', async () => {
    const failed = done({
      counts: { imported: 1, blocked: 1, skipped_stopped: 1, skipped_budget: 1, provider_error: 1, invalid_response: 1,
        unsupported_kind: 1, rate_limited: 0 },
      symbols: [
        { symbol: 'AAA', outcome: 'imported', dates: 2, latest_date: '2026-11-12', latest_close: '1.0000' },
        { symbol: 'BBB', outcome: 'blocked', dates: 0, latest_date: null, latest_close: null },
        { symbol: 'CCC', outcome: 'skipped_stopped', dates: 0, latest_date: null, latest_close: null },
        { symbol: 'DDD', outcome: 'skipped_budget', dates: 0, latest_date: null, latest_close: null },
        { symbol: 'EEE', outcome: 'provider_error', dates: 0, latest_date: null, latest_close: null },
        { symbol: 'FFF', outcome: 'invalid_response', dates: 0, latest_date: null, latest_close: null },
        { symbol: 'GGG', outcome: 'unsupported_kind', dates: 0, latest_date: null, latest_close: null },
      ],
    });
    const { user } = setup([failed]);
    await user.click(button());
    const table = await screen.findByRole('table', { name: /per-symbol results/i });
    const row = (symbol: string) => within(table).getByText(symbol).closest('tr') as HTMLElement;
    expect(row('BBB').textContent).toMatch(/blocked the request/i);
    expect(row('CCC').textContent).toMatch(/press was stopped/i);
    expect(row('DDD').textContent).toMatch(/request limit/i);
    expect(row('EEE').textContent).toMatch(/Yahoo error/i);
    expect(row('FFF').textContent).toMatch(/unusable response/i);
    expect(row('GGG').textContent).toMatch(/not a stock or ETF/i);
    expect(row('BBB').className).toMatch(/failed/);
    expect(row('AAA').className).not.toMatch(/failed/);
    expect(screen.getByRole('status').textContent).toMatch(/6 of 7 symbols were not updated/i);
    expect(screen.getByRole('alert').textContent).toMatch(/stopped early/i);
  });
  it('renders provider-derived strings as text, never as markup', async () => {
    const evil = '<img src=x onerror=alert(1)>';
    const { user, container } = setup([done({
      symbols: [{ symbol: evil, outcome: 'imported', dates: 1, latest_date: '2026-11-12', latest_close: '1.0000' }],
      counts: { imported: 1 },
    })]);
    await user.click(button());
    await screen.findByRole('table', { name: /per-symbol results/i });
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain(evil);
  });
  it('says so when another fetch is already running (409)', async () => {
    const { user } = setup([done()], { startPriceFetch: () => Promise.reject(new ApiError(409, 'busy')) });
    await user.click(button());
    expect((await screen.findByRole('alert')).textContent).toMatch(/already running/i);
    expect(button().disabled).toBe(false);
  });
  it('shows an interrupted fetch (server restarted) instead of hanging, and stops polling', async () => {
    const { user, api } = setup([running(1), { state: 'unknown' }]);
    await user.click(button());
    expect((await screen.findByRole('alert')).textContent).toMatch(/interrupted/i);
    const calls = (api.priceFetchStatus as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect((api.priceFetchStatus as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(calls);
    expect(button().disabled).toBe(false);
  });
  it('explains a failed press by its fixed code and that nothing was saved', async () => {
    for (const [error, pattern] of [['forbidden', /no longer valid.*nothing was saved/i],
      ['interrupted', /interrupted.*nothing was saved/i], ['internal', /failed.*nothing was saved/i]] as const) {
      const { user, unmount } = setup([{ state: 'failed', error, result: null }]);
      await user.click(button());
      expect((await screen.findByRole('alert')).textContent).toMatch(pattern);
      unmount();
    }
  });
  it('shows a network problem while polling and re-enables the button', async () => {
    const { user } = setup([running(0)], { priceFetchStatus: () => Promise.reject(new ApiError(0, 'network')) });
    await user.click(button());
    expect((await screen.findByRole('alert')).textContent).toMatch(/could not reach the server/i);
    expect(button().disabled).toBe(false);
  });
  it('stops polling when the screen goes away', async () => {
    const { user, api, unmount } = setup([running(0)]);
    await user.click(button());
    await waitFor(() => expect((api.priceFetchStatus as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBeGreaterThan(0));
    unmount();
    const calls = (api.priceFetchStatus as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect((api.priceFetchStatus as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(calls);
  });
  it('mentions how many symbols were omitted from the list when the server truncates it', async () => {
    const { user, container } = setup([done({ omitted: 7 })]);
    await user.click(button());
    await screen.findByRole('table', { name: /per-symbol results/i });
    expect(container.textContent).toMatch(/7 more symbols are not listed/i);
  });
});
