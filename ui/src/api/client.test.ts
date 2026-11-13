import { describe, expect, it, vi } from 'vitest';
import { ApiError, createApi } from './client';

type Call = { url: string; init: RequestInit };

function fakeFetch(responses: Array<{ status?: number; body?: unknown; headers?: Record<string, string> }>) {
  const calls: Call[] = [];
  const impl = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const next = responses.shift() ?? { status: 200, body: {} };
    return new Response(JSON.stringify(next.body ?? {}), {
      status: next.status ?? 200,
      headers: { 'content-type': 'application/json', ...(next.headers ?? {}) },
    });
  });
  return { impl: impl as unknown as typeof fetch, calls };
}

const SESSION = { user_id: 'u1', role: 'user', csrf: 'csrf-token-1' };

describe('createApi', () => {
  it('stores the CSRF token from login and sends it on unsafe requests only', async () => {
    const { impl, calls } = fakeFetch([{ body: SESSION }, { body: [] }, { status: 201, body: { id: 'p1', name: 'N' } }]);
    const api = createApi(impl);
    await api.login('tok');
    await api.portfolios();
    await api.createPortfolio('N');
    const headerOf = (i: number, name: string) => new Headers(calls[i]?.init.headers).get(name);
    expect(headerOf(0, 'x-csrf-token')).toBeNull();
    expect(headerOf(1, 'x-csrf-token')).toBeNull();
    expect(headerOf(2, 'x-csrf-token')).toBe('csrf-token-1');
    expect(headerOf(2, 'content-type')).toBe('application/json');
    expect(calls[2]?.init.credentials).toBe('same-origin');
    expect(calls[2]?.init.method).toBe('POST');
  });

  it('maps error responses to ApiError with the server code only', async () => {
    const { impl } = fakeFetch([{ status: 401, body: { error: 'unauthenticated' } }]);
    const api = createApi(impl);
    const error = await api.session().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).code).toBe('unauthenticated');
  });

  it('surfaces Retry-After on 429', async () => {
    const { impl } = fakeFetch([{ status: 429, body: { error: 'rate_limited' }, headers: { 'retry-after': '7' } }]);
    const error = (await createApi(impl).session().catch((e: unknown) => e)) as ApiError;
    expect(error.retryAfter).toBe(7);
  });

  it.each([
    ['holdings quantity', { body: [{ security_id: 's', symbol: 'A', kind: 'stock', quantity: 1.5 }] }, 'holdings'],
    ['valuation total', { body: { total: 100, priced_subtotal: '1', lines: [], policy: {} } }, 'valuation'],
    ['line market_value', { body: { total: null, priced_subtotal: '1', policy: {}, lines: [{ market_value: 2.5 }] } }, 'valuation'],
  ])('rejects numeric money fields in responses: %s', async (_name, response, which) => {
    const { impl } = fakeFetch([{ body: SESSION }, response]);
    const api = createApi(impl);
    await api.login('t');
    const call = which === 'holdings' ? api.holdings('p') : api.valuation('p', 'src');
    await expect(call).rejects.toMatchObject({ code: 'numeric_money' });
  });

  it('refuses to send a numeric quantity or value', async () => {
    const { impl, calls } = fakeFetch([{ body: SESSION }]);
    const api = createApi(impl);
    await api.login('t');
    await expect(
      api.importHolding('p', 'ABC', 5 as unknown as string, 'key-12345678'),
    ).rejects.toMatchObject({ code: 'numeric_money' });
    expect(calls).toHaveLength(1);
  });

  it('percent-encodes valuation query values (the + in a UTC offset)', async () => {
    const { impl, calls } = fakeFetch([{ body: SESSION }, { body: { total: null, priced_subtotal: '0', lines: [], policy: {} } }]);
    const api = createApi(impl);
    await api.login('t');
    await api.valuation('p 1', 'operator-entered', '2025-01-02T03:04:05+00:00');
    expect(calls[1]?.url).toBe(
      '/api/portfolios/p%201/valuation?source=operator-entered&as_of=2025-01-02T03%3A04%3A05%2B00%3A00',
    );
  });

  it('streams chat events with the CSRF header and parses JSON payloads', async () => {
    const sse = 'event: status\ndata: {"state":"started"}\n\nevent: explanation\ndata: {"text":"<b>hi</b>"}\n\n';
    const calls: Call[] = [];
    const impl = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      if (url === '/api/session') return new Response(JSON.stringify(SESSION), { status: 200 });
      return new Response(sse, { status: 200, headers: { 'content-type': 'text/event-stream' } });
    }) as unknown as typeof fetch;
    const api = createApi(impl);
    await api.login('t');
    const events = [];
    for await (const e of api.chat({ message: 'hello', portfolio_id: 'p1' })) events.push(e);
    expect(events).toEqual([
      { event: 'status', data: { state: 'started' } },
      { event: 'explanation', data: { text: '<b>hi</b>' } },
    ]);
    expect(new Headers(calls[1]?.init.headers).get('x-csrf-token')).toBe('csrf-token-1');
  });

  it('throws ApiError for pre-stream chat failures', async () => {
    const { impl } = fakeFetch([{ body: SESSION }, { status: 404, body: { error: 'not_found' } }]);
    const api = createApi(impl);
    await api.login('t');
    const iterate = async () => {
      for await (const e of api.chat({ message: 'x' })) void e;
    };
    await expect(iterate()).rejects.toMatchObject({ status: 404, code: 'not_found' });
  });
  it('posts the starter-data actions with the CSRF header and surfaces the 409 code', async () => {
    const { impl, calls } = fakeFetch([
      { body: SESSION },
      { body: { registered: 20, already_present: 0, symbols: 20 } },
      { status: 201, body: { id: 'p1', name: 'Sample portfolio (synthetic)', created: true } },
      { status: 409, body: { error: 'starter_instruments_missing' } },
    ]);
    const api = createApi(impl);
    await api.login('tok');
    expect(await api.registerStarterInstruments()).toEqual({ registered: 20, already_present: 0, symbols: 20 });
    expect((await api.createSamplePortfolio()).created).toBe(true);
    expect(calls[1]?.url).toBe('/api/operator/starter/instruments');
    expect(calls[2]?.url).toBe('/api/starter/sample-portfolio');
    for (const i of [1, 2]) {
      expect(calls[i]?.init.method).toBe('POST');
      expect(new Headers(calls[i]?.init.headers).get('x-csrf-token')).toBe('csrf-token-1');
    }
    const failure = await api.createSamplePortfolio().catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(409);
    expect((failure as ApiError).code).toBe('starter_instruments_missing');
  });
});
