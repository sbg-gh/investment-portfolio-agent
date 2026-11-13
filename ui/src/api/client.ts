import { parseSse } from './sse';
import type {
  ChatEvent, ChatRequest, Holding, Portfolio, PriceImportResult, Receipt, SamplePortfolioResult, SessionInfo,
  StarterInstrumentsResult, Valuation,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter: number | null;
  constructor(status: number, code: string, retryAfter: number | null = null) {
    super(code);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

// Fields that carry money or quantities: the wire format is a decimal string, never a number.
const MONEY_KEYS = new Set(['quantity', 'value', 'total', 'priced_subtotal', 'market_value', 'unit_value']);

function assertNoNumericMoney(node: unknown): void {
  if (Array.isArray(node)) {
    node.forEach(assertNoNumericMoney);
  } else if (node !== null && typeof node === 'object') {
    for (const [key, child] of Object.entries(node)) {
      if (MONEY_KEYS.has(key) && typeof child === 'number') throw new ApiError(502, 'numeric_money');
      assertNoNumericMoney(child);
    }
  }
}

function assertMoneyStrings(body: Record<string, unknown>): void {
  for (const key of MONEY_KEYS) {
    if (key in body && typeof body[key] !== 'string') throw new ApiError(0, 'numeric_money');
  }
}

async function failure(response: Response): Promise<ApiError> {
  let code = 'unknown';
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && typeof (body as { error?: unknown }).error === 'string') {
      code = (body as { error: string }).error;
    }
  } catch {
    // keep the generic code; never surface server text
  }
  const wait = Number.parseInt(response.headers.get('retry-after') ?? '', 10);
  return new ApiError(response.status, code, Number.isFinite(wait) ? wait : null);
}

export type OperatorObservation = {
  security_id: string;
  value: string;
  basis: 'price' | 'nav';
  source: string;
  observed_at: string;
  fetched_at?: string;
};

export function createApi(fetchImpl: typeof fetch = (...args) => fetch(...args)) {
  let csrf: string | null = null;

  async function send(method: string, path: string, body?: Record<string, unknown>): Promise<Response> {
    if (body) assertMoneyStrings(body);
    const headers = new Headers({ accept: 'application/json' });
    if (method !== 'GET') {
      headers.set('content-type', 'application/json');
      if (csrf) headers.set('x-csrf-token', csrf);
    }
    const init: RequestInit = { method, headers, credentials: 'same-origin' };
    if (body !== undefined) init.body = JSON.stringify(body);
    let response: Response;
    try {
      response = await fetchImpl(path, init);
    } catch {
      throw new ApiError(0, 'network');
    }
    if (!response.ok) throw await failure(response);
    return response;
  }

  async function json<T>(method: string, path: string, body?: Record<string, unknown>): Promise<T> {
    const parsed: unknown = await (await send(method, path, body)).json();
    assertNoNumericMoney(parsed);
    return parsed as T;
  }

  const portfolio = (id: string) => `/api/portfolios/${encodeURIComponent(id)}`;

  return {
    async login(token: string): Promise<SessionInfo> {
      const info = await json<SessionInfo>('POST', '/api/session', { token });
      csrf = info.csrf;
      return info;
    },
    async session(): Promise<SessionInfo> {
      const info = await json<SessionInfo>('GET', '/api/session');
      csrf = info.csrf;
      return info;
    },
    async logout(): Promise<void> {
      await send('DELETE', '/api/session');
      csrf = null;
    },
    portfolios: () => json<Portfolio[]>('GET', '/api/portfolios'),
    createPortfolio: (name: string) => json<Portfolio>('POST', '/api/portfolios', { name }),
    holdings: (id: string) => json<Holding[]>('GET', `${portfolio(id)}/holdings`),
    importHolding: (id: string, symbol: string, quantity: string, idempotency_key: string) =>
      json<{ security_id: string }>('POST', `${portfolio(id)}/holdings`, { symbol, quantity, idempotency_key }),
    removeHolding: (id: string, securityId: string, idempotency_key: string) =>
      json<{ security_id: string }>('DELETE', `${portfolio(id)}/holdings/${encodeURIComponent(securityId)}`, {
        idempotency_key,
      }),
    receipts: (id: string) => json<Receipt[]>('GET', `${portfolio(id)}/receipts`),
    valuation(id: string, source: string, asOf?: string, maxAgeDays?: number): Promise<Valuation> {
      const query = new URLSearchParams({ source });
      if (asOf) query.set('as_of', asOf);
      if (maxAgeDays !== undefined) query.set('max_age_days', String(maxAgeDays));
      // URLSearchParams encodes spaces as '+'; the server needs strict percent-encoding.
      return json<Valuation>('GET', `${portfolio(id)}/valuation?${query.toString().replace(/\+/g, '%20')}`);
    },
    sources: () => json<string[]>('GET', '/api/sources'),
    registerInstrument: (security_id: string, symbol: string, kind: string) =>
      json<{ security_id: string }>('POST', '/api/operator/instruments', { security_id, symbol, kind }),
    registerStarterInstruments: () =>
      json<StarterInstrumentsResult>('POST', '/api/operator/starter/instruments', {}),
    createSamplePortfolio: () => json<SamplePortfolioResult>('POST', '/api/starter/sample-portfolio', {}),
    importPrices: (source: string, csv_text: string, dry_run: boolean) =>
      json<PriceImportResult>('POST', '/api/operator/prices/import', { source, csv_text, dry_run }),
    addObservation: (observation: OperatorObservation) =>
      json<{ observation_id: string }>('POST', '/api/operator/observations', { ...observation }),
    async *chat(request: ChatRequest, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
      const headers = new Headers({ accept: 'text/event-stream', 'content-type': 'application/json' });
      if (csrf) headers.set('x-csrf-token', csrf);
      const init: RequestInit = {
        method: 'POST', headers, credentials: 'same-origin', body: JSON.stringify(request),
      };
      if (signal) init.signal = signal;
      let response: Response;
      try {
        response = await fetchImpl('/api/chat', init);
      } catch {
        throw new ApiError(0, 'network');
      }
      if (!response.ok) throw await failure(response);
      if (!response.body) throw new ApiError(502, 'no_stream');
      for await (const raw of parseSse(response.body)) {
        let data: unknown;
        try {
          data = JSON.parse(raw.data);
        } catch {
          throw new ApiError(502, 'bad_event');
        }
        assertNoNumericMoney(data);
        yield { event: raw.event, data } as ChatEvent;
      }
    },
  };
}

export type Api = ReturnType<typeof createApi>;
