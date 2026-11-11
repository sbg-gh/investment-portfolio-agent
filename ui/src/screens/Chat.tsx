import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import type { EvidenceRow, Portfolio } from '../api/types';
import { Page, Problem } from '../components/Page';
import { useApp } from '../context';
import { describeError } from '../errors';

const MAX_MESSAGE = 2000;
const STREAM_ERRORS: Record<string, string> = {
  busy: 'The server is busy. Please try again shortly.',
  not_found: 'That portfolio was not found.',
  internal: 'Something went wrong while answering. Please try again.',
};

type Turn =
  | { who: 'You'; text: string }
  | { who: 'Assistant'; text: string; evidence: EvidenceRow[]; proposals: unknown[] };

// Everything below renders as React text nodes: model output and evidence are untrusted data.
function Evidence({ rows }: { rows: EvidenceRow[] }) {
  if (rows.length === 0) return null;
  return (
    <table>
      <caption>Evidence</caption>
      <thead>
        <tr><th scope="col">Security</th><th scope="col">Status</th><th scope="col">Value</th><th scope="col">Observed</th><th scope="col">As of</th></tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            <td>{r.security_id}</td>
            <td>{r.status}</td>
            <td className="num">{r.market_value ?? '—'}</td>
            <td>{r.observed_at ?? 'No observation'}</td>
            <td>{r.as_of}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Chat() {
  const { api, announce } = useApp();
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [portfolioId, setPortfolioId] = useState('');
  const [message, setMessage] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [working, setWorking] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const conversation = useRef<string | undefined>(undefined);
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let live = true;
    api.portfolios().then((rows) => live && setPortfolios(rows), (e: unknown) => live && setProblem(describeError(e)));
    return () => {
      live = false;
      abort.current?.abort();
    };
  }, [api]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setWorking(true);
    setUnreachable(false);
    setProblem(null);
    setMessage('');
    setTurns((t) => [...t, { who: 'You', text }]);
    let evidence: EvidenceRow[] = [];
    let proposals: unknown[] = [];
    try {
      const request = { message: text, ...(portfolioId ? { portfolio_id: portfolioId } : {}),
        ...(conversation.current ? { conversation_id: conversation.current } : {}) };
      for await (const item of api.chat(request, controller.signal)) {
        if (item.event === 'evidence') evidence = item.data;
        else if (item.event === 'proposals') {
          proposals = item.data;
          setTurns((t) => t.map((turn, i) => (i === t.length - 1 && turn.who === 'Assistant' ? { ...turn, proposals } : turn)));
        }
        else if (item.event === 'explanation') {
          setTurns((t) => [...t, { who: 'Assistant', text: item.data.text, evidence, proposals }]);
          announce('Answer received');
        } else if (item.event === 'done') {
          conversation.current = item.data.conversation_id;
          setUnreachable(item.data.model_status === 'unavailable');
        } else if (item.event === 'error') {
          setProblem(STREAM_ERRORS[item.data.error] ?? STREAM_ERRORS.internal ?? null);
        }
      }
    } catch (error) {
      if (controller.signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
        announce('Stopped');
      } else {
        setProblem(describeError(error instanceof ApiError ? error : new Error('stream')));
      }
    } finally {
      abort.current = null;
      setBusy(false);
      setWorking(false);
      box.current?.focus();
    }
  }

  return (
    <Page title="Chat">
      <p className="hint">
        Answers are explanations over your recorded data. Nothing proposed here is ever executed by this screen.
      </p>
      {unreachable && (
        <p role="alert" className="problem">
          The model is unreachable. Showing deterministic facts from your recorded data only.
        </p>
      )}
      <div role="log" aria-label="Conversation" className="log">
        {turns.map((turn, i) => (
          <div key={i} className={turn.who === 'You' ? 'turn you' : 'turn assistant'}>
            <p><strong>{turn.who}:</strong> <span>{turn.text}</span></p>
            {turn.who === 'Assistant' && <Evidence rows={turn.evidence} />}
            {turn.who === 'Assistant' && turn.proposals.length > 0 && (
              <details>
                <summary>Proposed actions ({turn.proposals.length}), not executed</summary>
                <pre>{JSON.stringify(turn.proposals, null, 2)}</pre>
              </details>
            )}
          </div>
        ))}
        {working && <p>Working…</p>}
      </div>
      <Problem message={problem} />
      <form onSubmit={send}>
        <label htmlFor="chat-portfolio">Portfolio (optional)</label>
        <select id="chat-portfolio" value={portfolioId} disabled={busy} onChange={(e) => setPortfolioId(e.target.value)}>
          <option value="">None</option>
          {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <label htmlFor="chat-message">Your message</label>
        <textarea id="chat-message" ref={box} value={message} maxLength={MAX_MESSAGE} rows={3}
          onChange={(e) => setMessage(e.target.value)} />
        {busy ? (
          <button type="button" onClick={() => abort.current?.abort()}>Stop</button>
        ) : (
          <button type="submit">Send</button>
        )}
      </form>
    </Page>
  );
}
