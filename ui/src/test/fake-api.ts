import { vi } from 'vitest';
import type { Api } from '../api/client';

/** Test double for the API: every method resolves to an empty-but-valid value unless overridden. */
export function fakeApi(overrides: Partial<Record<keyof Api, unknown>> = {}): Api {
  const base: Record<string, unknown> = {
    login: () => Promise.resolve({ user_id: 'u', role: 'user', csrf: 'c' }),
    session: () => Promise.resolve({ user_id: 'u', role: 'user', csrf: 'c' }),
    logout: () => Promise.resolve(),
    portfolios: () => Promise.resolve([]),
    createPortfolio: (name: string) => Promise.resolve({ id: 'p-new', name }),
    holdings: () => Promise.resolve([]),
    importHolding: () => Promise.resolve({ security_id: 's' }),
    removeHolding: () => Promise.resolve({ security_id: 's' }),
    receipts: () => Promise.resolve([]),
    valuation: () => Promise.reject(new Error('valuation not stubbed')),
    sources: () => Promise.resolve([]),
    registerInstrument: () => Promise.resolve({ security_id: 's' }),
    registerStarterInstruments: () => Promise.resolve({ registered: 20, already_present: 0, symbols: 20 }),
    createSamplePortfolio: () => Promise.resolve({ id: 'p-sample', name: 'Sample portfolio (synthetic)', created: true }),
    importPrices: (source: string, _text: string, dry_run: boolean) =>
      Promise.resolve({ dry_run, source, total_rows: 0, counts: {}, ignored_columns: [], rows: [] }),
    addObservation: () => Promise.resolve({ observation_id: 'o' }),
    chat: async function* () {
      /* no events */
    },
  };
  const merged = { ...base, ...overrides };
  return Object.fromEntries(Object.entries(merged).map(([k, v]) => [k, vi.fn(v as never)])) as unknown as Api;
}
