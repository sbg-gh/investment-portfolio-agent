import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { createApi, type Api } from './api/client';
import type { SessionInfo } from './api/types';
import { Page } from './components/Page';
import { AppContext } from './context';
import { hrefFor, useRoute } from './router';
import { Chat } from './screens/Chat';
import { Holdings } from './screens/Holdings';
import { Login } from './screens/Login';
import { Operator } from './screens/Operator';
import { Portfolios } from './screens/Portfolios';
import { Valuation } from './screens/Valuation';

export const UNAUTHENTICATED_EVENT = 'pa:unauthenticated';

export function App({ api: injected }: { api?: Api }) {
  const api = useMemo(() => injected ?? createApi(), [injected]);
  const route = useRoute();
  const [session, setSession] = useState<SessionInfo | null | undefined>(undefined);
  const [message, setMessage] = useState('');
  const first = useRef(true);
  const main = useRef<HTMLElement>(null);

  useEffect(() => {
    let live = true;
    api.session().then((info) => live && setSession(info), () => live && setSession(null));
    const lost = () => setSession(null);
    window.addEventListener(UNAUTHENTICATED_EVENT, lost);
    return () => {
      live = false;
      window.removeEventListener(UNAUTHENTICATED_EVENT, lost);
    };
  }, [api]);

  const routeKey = JSON.stringify(route) + String(session === undefined ? 'loading' : session ? 'in' : 'out');
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    document.querySelector<HTMLElement>('main h1')?.focus();
  }, [routeKey]);

  const announce = useCallback((text: string) => setMessage(text), []);

  async function signOut() {
    try {
      await api.logout();
    } finally {
      setSession(null);
      setMessage('Signed out');
    }
  }

  function skip(event: MouseEvent) {
    event.preventDefault();
    main.current?.focus();
  }

  let screen;
  if (session === undefined) screen = <Page title="Loading" />;
  else if (session === null) screen = null;
  else if (route.name === 'chat') screen = <Chat />;
  else if (route.name === 'operator') screen = <Operator symbol={route.symbol} />;
  else if (route.name === 'holdings') screen = <Holdings key={route.id} portfolioId={route.id} />;
  else if (route.name === 'valuation') screen = <Valuation key={route.id} portfolioId={route.id} />;
  else if (route.name === 'not-found') screen = <Page title="Page not found"><p><a href={hrefFor({ name: 'portfolios' })}>Go to portfolios</a></p></Page>;
  else screen = <Portfolios />;

  const current = (name: string) => (route.name === name ? 'page' : undefined);
  const state = session
    ? { api, session, announce }
    : { api, session: { user_id: '', role: 'user' as const, csrf: '' }, announce };

  return (
    <AppContext.Provider value={state}>
      <a className="skip-link" href="#main" onClick={skip}>Skip to main content</a>
      <header>
        <span className="brand">Portfolio Agent</span>
        {session && (
          <>
            <nav aria-label="Primary">
              <a href={hrefFor({ name: 'portfolios' })} aria-current={current('portfolios')}>Portfolios</a>
              <a href={hrefFor({ name: 'chat' })} aria-current={current('chat')}>Chat</a>
              {session.role === 'operator' && (
                <a href={hrefFor({ name: 'operator' })} aria-current={current('operator')}>Operator</a>
              )}
            </nav>
            <button type="button" onClick={() => void signOut()}>Sign out</button>
          </>
        )}
      </header>
      <main id="main" ref={main} tabIndex={-1}>
        {session === null ? <Login onLogin={setSession} /> : screen}
      </main>
      <div aria-live="polite" className="sr-only">{message}</div>
    </AppContext.Provider>
  );
}
