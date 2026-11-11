import { useEffect, useState } from 'react';

export type Route =
  | { name: 'login' }
  | { name: 'portfolios' }
  | { name: 'holdings'; id: string }
  | { name: 'valuation'; id: string }
  | { name: 'chat' }
  | { name: 'operator' }
  | { name: 'not-found' };

// The hash never reaches the server, so ids in routes cannot appear in any access log.
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, '');
  const parts = path === '' ? [] : path.split('/');
  const [first, id, third] = parts;
  if (parts.length === 0 || (parts.length === 1 && first === 'portfolios')) return { name: 'portfolios' };
  if (parts.length === 1 && first === 'login') return { name: 'login' };
  if (parts.length === 1 && first === 'chat') return { name: 'chat' };
  if (parts.length === 1 && first === 'operator') return { name: 'operator' };
  if (first === 'portfolios' && id !== undefined && ID.test(id)) {
    if (parts.length === 2) return { name: 'holdings', id };
    if (parts.length === 3 && third === 'valuation') return { name: 'valuation', id };
  }
  return { name: 'not-found' };
}

export function hrefFor(route: Route): string {
  switch (route.name) {
    case 'holdings':
      return `#/portfolios/${encodeURIComponent(route.id)}`;
    case 'valuation':
      return `#/portfolios/${encodeURIComponent(route.id)}/valuation`;
    case 'not-found':
      return '#/';
    case 'portfolios':
      return '#/portfolios';
    default:
      return `#/${route.name}`;
  }
}

export function useRoute(): Route {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return parseRoute(hash);
}
