import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/client';
import { fakeApi } from '../test/fake-api';
import { renderScreen } from '../test/render';
import { Operator } from './Operator';

describe('Operator', () => {
  it('refuses to show the forms to a non-operator', async () => {
    renderScreen(<Operator />, fakeApi(), 'user');
    expect((await screen.findByRole('alert')).textContent).toMatch(/operator role/i);
    expect(screen.queryByRole('button', { name: /register instrument/i })).toBeNull();
  });

  it('states that there is no live market data and that instruments are attested', async () => {
    const { container } = renderScreen(<Operator />, fakeApi(), 'operator');
    expect(await screen.findByText(/no live market data/i)).toBeTruthy();
    expect(screen.getByText(/operator-attested/i)).toBeTruthy();
    expect(await axe(container)).toHaveNoViolations();
  });

  it('registers an instrument', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    renderScreen(<Operator />, api, 'operator');
    await user.type(await screen.findByLabelText(/security id/i, { selector: '#reg-id' }), 'sec-9');
    await user.type(screen.getByLabelText(/^symbol/i), 'ZZZ');
    await user.selectOptions(screen.getByLabelText(/kind/i), 'etf');
    await user.click(screen.getByRole('button', { name: /register instrument/i }));
    expect(api.registerInstrument).toHaveBeenCalledWith('sec-9', 'ZZZ', 'etf');
    expect((await screen.findByRole('status')).textContent).toMatch(/registered/i);
  });

  it('records an observation with a decimal-string value and UTC timestamps', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    renderScreen(<Operator />, api, 'operator');
    await user.type(await screen.findByLabelText(/security id/i, { selector: '#obs-id' }), 'sec-9');
    await user.type(screen.getByLabelText(/^value/i), '123.45');
    await user.clear(screen.getByLabelText(/source/i));
    await user.type(screen.getByLabelText(/source/i), 'operator-entered');
    await user.type(screen.getByLabelText(/observed at/i), '2025-03-01T10:30');
    await user.click(screen.getByRole('button', { name: /record observation/i }));
    expect(api.addObservation).toHaveBeenCalledWith(
      expect.objectContaining({
        security_id: 'sec-9', value: '123.45', basis: 'price', source: 'operator-entered',
        observed_at: '2025-03-01T10:30:00+00:00',
      }),
    );
  });

  it('blocks a non-decimal value', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    renderScreen(<Operator />, api, 'operator');
    await user.type(await screen.findByLabelText(/security id/i, { selector: '#obs-id' }), 'sec-9');
    await user.type(screen.getByLabelText(/^value/i), '1.5e3');
    await user.type(screen.getByLabelText(/observed at/i), '2025-03-01T10:30');
    await user.click(screen.getByRole('button', { name: /record observation/i }));
    expect(api.addObservation).not.toHaveBeenCalled();
    expect((await screen.findByRole('alert')).textContent).toMatch(/plain decimal/i);
  });
  it('registers the starter instruments, shows counts and says no prices are included', async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      registerStarterInstruments: () => Promise.resolve({ registered: 19, already_present: 1, symbols: 20 }),
    });
    const { container } = renderScreen(<Operator />, api, 'operator');
    await user.click(await screen.findByRole('button', { name: /register starter instruments/i }));
    expect(api.registerStarterInstruments).toHaveBeenCalledTimes(1);
    const status = await screen.findByText(/19 registered/i);
    expect(status.textContent).toMatch(/1 already present/i);
    expect(status.textContent).toMatch(/20/);
    expect(screen.getByText(/no prices are included/i)).toBeTruthy();
    expect(screen.getByText(/skhy/i).textContent).toMatch(/not verified/i);
    expect(screen.getByRole('link', { name: /portfolios/i }).getAttribute('href')).toBe('#/portfolios');
    expect(await axe(container)).toHaveNoViolations();
  });
  it('shows a fixed message when starter registration fails', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ registerStarterInstruments: () => Promise.reject(new ApiError(403, 'forbidden')) });
    renderScreen(<Operator />, api, 'operator');
    await user.click(await screen.findByRole('button', { name: /register starter instruments/i }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/operator role required/i);
  });
  it('prefills the registration form from the route symbol with the registry-style id', async () => {
    renderScreen(<Operator symbol="AMD" />, fakeApi(), 'operator');
    expect((await screen.findByLabelText(/^symbol/i) as HTMLInputElement).value).toBe('AMD');
    expect((screen.getByLabelText(/security id/i, { selector: '#reg-id' }) as HTMLInputElement).value).toBe('op:amd');
  });
});
