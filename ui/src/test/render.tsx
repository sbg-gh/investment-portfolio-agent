import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import type { Api } from '../api/client';
import { AppContext } from '../context';
import type { Role } from '../api/types';

export function renderScreen(ui: ReactElement, api: Api, role: Role = 'user') {
  const announce = vi.fn();
  const session = { user_id: 'u', role, csrf: 'c' };
  const result = render(<AppContext.Provider value={{ api, session, announce }}>{ui}</AppContext.Provider>);
  return { ...result, announce };
}
