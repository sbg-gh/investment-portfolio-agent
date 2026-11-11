import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { afterEach, describe, expect, it } from 'vitest';
import { App } from './App';
import { ApiError } from './api/client';
import { fakeApi } from './test/fake-api';

afterEach(() => {
  window.location.hash = '';
});

describe('App shell', () => {
  it('shows the login screen when there is no session', async () => {
    const api = fakeApi({ session: () => Promise.reject(new ApiError(401, 'unauthenticated')) });
    render(<App api={api} />);
    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeTruthy();
  });

  it('has a skip link first, landmarks, and a polite live region', async () => {
    const api = fakeApi();
    const { container } = render(<App api={api} />);
    await screen.findByRole('heading', { name: /portfolios/i, level: 1 });
    const skip = screen.getByRole('link', { name: /skip to main content/i });
    expect(skip.getAttribute('href')).toBe('#main');
    expect(screen.getByRole('banner')).toBeTruthy();
    expect(screen.getByRole('navigation', { name: /primary/i })).toBeTruthy();
    expect(screen.getByRole('main')).toBeTruthy();
    expect(document.querySelector('[aria-live="polite"]')).not.toBeNull();
    expect(await axe(container)).toHaveNoViolations();
  });

  it('moves focus to the new page heading on route change', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<App api={api} />);
    await screen.findByRole('heading', { name: /portfolios/i, level: 1 });
    await user.click(screen.getByRole('link', { name: /^chat$/i }));
    const heading = await screen.findByRole('heading', { name: /chat/i, level: 1 });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });

  it('hides the operator link from non-operators and shows it to operators', async () => {
    const { unmount } = render(<App api={fakeApi()} />);
    await screen.findByRole('heading', { name: /portfolios/i, level: 1 });
    expect(screen.queryByRole('link', { name: /operator/i })).toBeNull();
    unmount();
    render(<App api={fakeApi({ session: () => Promise.resolve({ user_id: 'o', role: 'operator', csrf: 'c' }) })} />);
    expect(await screen.findByRole('link', { name: /operator/i })).toBeTruthy();
  });

  it('logs out and returns to sign in', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} />);
    await screen.findByRole('heading', { name: /portfolios/i, level: 1 });
    await user.click(screen.getByRole('button', { name: /sign out/i }));
    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeTruthy();
  });
});
