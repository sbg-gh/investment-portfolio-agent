import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import { fakeApi } from '../test/fake-api';
import { renderScreen } from '../test/render';
import { Portfolios } from './Portfolios';

describe('Portfolios', () => {
  it('lists portfolios as links to holdings and valuation', async () => {
    const api = fakeApi({ portfolios: () => Promise.resolve([{ id: 'p-1', name: 'Retirement' }]) });
    const { container } = renderScreen(<Portfolios />, api);
    const holdings = await screen.findByRole('link', { name: 'Retirement' });
    expect(holdings.getAttribute('href')).toBe('#/portfolios/p-1');
    expect(screen.getByRole('link', { name: /valuation for retirement/i }).getAttribute('href')).toBe(
      '#/portfolios/p-1/valuation',
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it('renders portfolio names as inert text', async () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const api = fakeApi({ portfolios: () => Promise.resolve([{ id: 'p-1', name: hostile }]) });
    const { container } = renderScreen(<Portfolios />, api);
    await screen.findByText(hostile);
    expect(container.querySelector('img')).toBeNull();
  });

  it('says so when there are none, and creates one from the form', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const { announce } = renderScreen(<Portfolios />, api);
    expect(await screen.findByText(/no portfolios yet/i)).toBeTruthy();
    await user.type(screen.getByLabelText(/portfolio name/i), 'Taxable');
    await user.click(screen.getByRole('button', { name: /create portfolio/i }));
    expect(api.createPortfolio).toHaveBeenCalledWith('Taxable');
    expect(await screen.findByRole('link', { name: 'Taxable' })).toBeTruthy();
    expect(announce).toHaveBeenCalledWith(expect.stringMatching(/created/i));
  });
  it('creates the synthetic sample portfolio from the button and lists it', async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      createSamplePortfolio: () => Promise.resolve({ id: 'p-s', name: 'Sample portfolio (synthetic)', created: true }),
    });
    const { container, announce } = renderScreen(<Portfolios />, api);
    await user.click(await screen.findByRole('button', { name: /create sample portfolio/i }));
    expect(api.createSamplePortfolio).toHaveBeenCalledTimes(1);
    expect((await screen.findByRole('link', { name: 'Sample portfolio (synthetic)' })).getAttribute('href')).toBe(
      '#/portfolios/p-s',
    );
    expect(announce).toHaveBeenCalledWith(expect.stringMatching(/sample portfolio/i));
    expect(screen.queryByRole('button', { name: /create sample portfolio/i })).toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });
  it('labels the sample as synthetic and says it has no prices', async () => {
    renderScreen(<Portfolios />, fakeApi());
    expect((await screen.findByText(/synthetic/i)).textContent).toMatch(/not your real positions/i);
    expect(screen.getByText(/no prices/i)).toBeTruthy();
  });
  it('hides the button when the sample portfolio already exists', async () => {
    const api = fakeApi({
      portfolios: () => Promise.resolve([{ id: 'p-s', name: 'Sample portfolio (synthetic)' }]),
    });
    renderScreen(<Portfolios />, api);
    await screen.findByRole('link', { name: 'Sample portfolio (synthetic)' });
    expect(screen.queryByRole('button', { name: /create sample portfolio/i })).toBeNull();
  });
  it('points an operator to the Operator screen when instruments are missing', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ createSamplePortfolio: () => Promise.reject(new ApiError(409, 'starter_instruments_missing')) });
    renderScreen(<Portfolios />, api, 'operator');
    await user.click(await screen.findByRole('button', { name: /create sample portfolio/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/register the starter instruments first/i);
    expect(within(alert).getByRole('link', { name: /operator/i }).getAttribute('href')).toBe('#/operator');
  });
  it('tells a non-operator that an operator must register the instruments', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ createSamplePortfolio: () => Promise.reject(new ApiError(409, 'starter_instruments_missing')) });
    renderScreen(<Portfolios />, api, 'user');
    await user.click(await screen.findByRole('button', { name: /create sample portfolio/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/an operator must register the starter instruments first/i);
    expect(within(alert).queryByRole('link')).toBeNull();
  });
});
