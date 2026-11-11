import { useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import type { SessionInfo } from '../api/types';
import { Page, Problem } from '../components/Page';
import { useApp } from '../context';

export function Login({ onLogin }: { onLogin: (info: SessionInfo) => void }) {
  const { api } = useApp();
  const [token, setToken] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      const info = await api.login(token);
      setToken('');
      onLogin(info);
    } catch (error) {
      setToken('');
      if (error instanceof ApiError && error.status === 429) {
        setProblem(error.retryAfter === null ? 'Too many attempts. Wait a moment.' : `Too many attempts. Try again in ${error.retryAfter} seconds.`);
      } else {
        setProblem('Could not sign in. Check the token and try again.');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page title="Sign in">
      <p>Paste the access token printed by the trusted operator command when your credential was issued.</p>
      <form onSubmit={submit}>
        <label htmlFor="token">Access token</label>
        <input id="token" type="password" autoComplete="off" value={token} required
          onChange={(e) => setToken(e.target.value)} />
        <button type="submit" disabled={busy}>Sign in</button>
      </form>
      <Problem message={problem} />
    </Page>
  );
}
