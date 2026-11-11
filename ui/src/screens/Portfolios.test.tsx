import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
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
});
