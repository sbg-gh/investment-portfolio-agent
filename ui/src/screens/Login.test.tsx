import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import { fakeApi } from '../test/fake-api';
import { renderScreen } from '../test/render';
import { Login } from './Login';

describe('Login', () => {
  it('uses a labelled password field and submits the token', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    const onLogin = vi.fn();
    const { container } = renderScreen(<Login onLogin={onLogin} />, api);
    const field = screen.getByLabelText(/access token/i) as HTMLInputElement;
    expect(field.type).toBe('password');
    expect(field.autocomplete).toBe('off');
    expect(await axe(container)).toHaveNoViolations();
    await user.type(field, 'secret-token');
    await user.click(screen.getByRole('button', { name: /sign in/i }));
    expect(api.login).toHaveBeenCalledWith('secret-token');
    expect(onLogin).toHaveBeenCalled();
    expect(field.value).toBe('');
  });

  it('shows one generic message for a rejected token', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ login: () => Promise.reject(new ApiError(401, 'unauthenticated')) });
    renderScreen(<Login onLogin={vi.fn()} />, api);
    await user.type(screen.getByLabelText(/access token/i), 'bad');
    await user.click(screen.getByRole('button', { name: /sign in/i }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/could not sign in/i);
  });

  it('tells the user how long to wait when rate limited', async () => {
    const user = userEvent.setup();
    const api = fakeApi({ login: () => Promise.reject(new ApiError(429, 'rate_limited', 7)) });
    renderScreen(<Login onLogin={vi.fn()} />, api);
    await user.type(screen.getByLabelText(/access token/i), 'x');
    await user.click(screen.getByRole('button', { name: /sign in/i }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/7 seconds/);
  });
});
