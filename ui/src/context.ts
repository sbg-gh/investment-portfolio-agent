import { createContext, useContext } from 'react';
import type { Api } from './api/client';
import type { SessionInfo } from './api/types';

export type AppState = { api: Api; session: SessionInfo; announce: (message: string) => void };

export const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const value = useContext(AppContext);
  if (!value) throw new Error('AppContext missing');
  return value;
}
