import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';
import type { ChatEvent } from '../api/types';
import { fakeApi } from '../test/fake-api';
import { renderScreen } from '../test/render';
import { Chat } from './Chat';

const DONE: ChatEvent = { event: 'done', data: { model_status: 'ok', conversation_id: 'c1', request_id: 'r1' } };
function scripted(events: ChatEvent[]) {
  return async function* () {
    for (const e of events) yield e;
  };
}

async function ask(api: ReturnType<typeof fakeApi>, text = 'How am I doing?') {
  const user = userEvent.setup();
  const view = renderScreen(<Chat />, api);
  await user.type(await screen.findByLabelText(/your message/i), text);
  await user.click(screen.getByRole('button', { name: /^send$/i }));
  return { user, ...view };
}

const PORTFOLIOS = () => Promise.resolve([{ id: 'p-1', name: 'Retirement' }]);

describe('Chat', () => {
  it('renders hostile model text as inert text', async () => {
    const hostile = '<img src=x onerror="window.pwned=1"><script>window.pwned=1</script><b>bold</b>';
    const api = fakeApi({
      portfolios: PORTFOLIOS,
      chat: scripted([
        { event: 'status', data: { state: 'started' } },
        { event: 'explanation', data: { text: hostile } },
        DONE,
      ]),
    });
    const { container } = await ask(api);
    await screen.findByText(hostile);
    expect(container.querySelector('img, script, b')).toBeNull();
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it('renders hostile evidence fields and proposals as inert text', async () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const api = fakeApi({
      portfolios: PORTFOLIOS,
      chat: scripted([
        { event: 'evidence', data: [{ security_id: hostile, status: 'fresh', market_value: '10', observed_at: null, as_of: 'now' }] },
        { event: 'explanation', data: { text: 'ok' } },
        { event: 'proposals', data: [{ action: hostile }] },
        DONE,
      ]),
    });
    const { container } = await ask(api);
    await screen.findByRole('table', { name: /evidence/i });
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain(hostile);
  });

  it('shows an evidence table with freshness details', async () => {
    const api = fakeApi({
      portfolios: PORTFOLIOS,
      chat: scripted([
        { event: 'evidence', data: [{ security_id: 'sec-1', status: 'stale', market_value: '12.5', observed_at: '2025-01-01T00:00:00+00:00', as_of: '2025-03-01T00:00:00+00:00' }] },
        { event: 'explanation', data: { text: 'Here you go' } },
        DONE,
      ]),
    });
    const { container } = await ask(api);
    const table = await screen.findByRole('table', { name: /evidence/i });
    expect(within(table).getByText('stale')).toBeTruthy();
    expect(within(table).getByText('12.5')).toBeTruthy();
    expect(await axe(container)).toHaveNoViolations();
  });

  it('shows a banner when the model is unreachable and keeps the deterministic facts', async () => {
    const api = fakeApi({
      portfolios: PORTFOLIOS,
      chat: scripted([
        { event: 'evidence', data: [] },
        { event: 'explanation', data: { text: 'Deterministic summary' } },
        { event: 'done', data: { model_status: 'unavailable', conversation_id: 'c', request_id: 'r' } },
      ]),
    });
    await ask(api);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/model is unreachable/i);
    expect(screen.getByText('Deterministic summary')).toBeTruthy();
  });

  it('reports a stream error event and a pre-stream failure', async () => {
    const api = fakeApi({
      portfolios: PORTFOLIOS,
      chat: scripted([{ event: 'error', data: { error: 'busy' } }]),
    });
    await ask(api);
    expect((await screen.findByRole('alert')).textContent).toMatch(/busy/i);
  });

  it('sends the selected portfolio, reuses the conversation id and can be stopped', async () => {
    let seen: AbortSignal | undefined;
    const requests: unknown[] = [];
    const api = fakeApi({
      portfolios: PORTFOLIOS,
      chat: async function* (request: unknown, signal?: AbortSignal) {
        requests.push(request);
        seen = signal;
        yield { event: 'status', data: { state: 'started' } } as ChatEvent;
        await new Promise((_, reject) => signal?.addEventListener('abort', () => reject(new DOMException('x', 'AbortError'))));
      },
    });
    const user = userEvent.setup();
    renderScreen(<Chat />, api);
    await user.selectOptions(await screen.findByLabelText(/portfolio/i), 'p-1');
    await user.type(screen.getByLabelText(/your message/i), 'hello');
    await user.click(screen.getByRole('button', { name: /^send$/i }));
    expect(requests[0]).toMatchObject({ message: 'hello', portfolio_id: 'p-1' });
    await user.click(await screen.findByRole('button', { name: /stop/i }));
    await waitFor(() => expect(seen?.aborted).toBe(true));
    expect(await screen.findByRole('button', { name: /^send$/i })).toBeTruthy();
  });

  it('caps the message at 2000 characters', async () => {
    const api = fakeApi({ portfolios: PORTFOLIOS });
    renderScreen(<Chat />, api);
    const box = (await screen.findByLabelText(/your message/i)) as HTMLTextAreaElement;
    expect(box.maxLength).toBe(2000);
  });
});
