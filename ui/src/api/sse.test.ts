import { describe, expect, it } from 'vitest';
import { parseSse, type SseEvent } from './sse';

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

async function collect(chunks: string[]): Promise<SseEvent[]> {
  const out: SseEvent[] = [];
  for await (const event of parseSse(streamOf(chunks))) out.push(event);
  return out;
}

describe('parseSse', () => {
  it('parses events and ignores keep-alive comments', async () => {
    const events = await collect([
      'event: status\ndata: {"state":"started"}\n\n',
      ': keep-alive\n\n',
      'event: done\ndata: {"model_status":"ok"}\n\n',
    ]);
    expect(events).toEqual([
      { event: 'status', data: '{"state":"started"}' },
      { event: 'done', data: '{"model_status":"ok"}' },
    ]);
  });
  it('reassembles events split across chunks, even mid-character', async () => {
    const encoder = new TextEncoder();
    const bytes = encoder.encode('event: explanation\ndata: {"text":"héllo"}\n\n');
    const cut = 38;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut));
        controller.enqueue(bytes.slice(cut));
        controller.close();
      },
    });
    const out: SseEvent[] = [];
    for await (const event of parseSse(stream)) out.push(event);
    expect(out).toEqual([{ event: 'explanation', data: '{"text":"héllo"}' }]);
  });
  it('accepts CRLF line endings', async () => {
    expect(await collect(['event: a\r\ndata: 1\r\n\r\n'])).toEqual([{ event: 'a', data: '1' }]);
  });
  it('drops an incomplete trailing event', async () => {
    expect(await collect(['event: a\ndata: 1\n\nevent: b\ndata: 2'])).toEqual([{ event: 'a', data: '1' }]);
  });
  it('bounds a single event so a hostile stream cannot exhaust memory', async () => {
    await expect(collect(['event: a\ndata: ' + 'x'.repeat(300000)])).rejects.toThrow(/too large/);
  });
});
