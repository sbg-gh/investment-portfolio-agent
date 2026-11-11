import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
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
});
