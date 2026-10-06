import { describe, expect, it, vi } from 'vitest';
import { askStream, askStreamEndpoint, createConversation } from '@/lib/chat-client';
import { sseFrame, sseStreamFromFrames } from '@/lib/sse';

const CITE = [{ title: 'The trace_id contract', path: 'architecture/tracing.md' }];
const TRACE = `0eb00${'a'.repeat(27)}`;

function sseResponse(frames: string[], status = 200) {
  return new Response(sseStreamFromFrames(frames), {
    status,
    headers: { 'content-type': 'text/event-stream', 'x-trace-id': TRACE },
  });
}

function handlers() {
  return { onSources: vi.fn(), onToken: vi.fn(), onDone: vi.fn(), onError: vi.fn() };
}

describe('askStream (browser → BFF)', () => {
  it('posts the question and dispatches sources → tokens → done with [{title, path}]', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        sseResponse([
          sseFrame('sources', { sources: CITE, count: 3 }),
          sseFrame('token', { text: 'Hel' }),
          sseFrame('token', { text: 'lo [1]' }),
          sseFrame('done', { sources: CITE, input_tokens: 3, output_tokens: 2 }),
        ]),
      );
    const h = handlers();
    await askStream(askStreamEndpoint('c1'), 'hi', h, { fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('/api/v1/conversations/c1/ask/stream');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ question: 'hi' });
    expect(h.onSources).toHaveBeenCalledWith(CITE);
    expect(h.onToken.mock.calls.map((c) => c[0]).join('')).toBe('Hello [1]');
    expect(h.onDone).toHaveBeenCalledWith({ citations: CITE, inputTokens: 3, outputTokens: 2 });
    expect(h.onError).not.toHaveBeenCalled();
  });

  it('drops citations that are not {title, path}', async () => {
    const h = handlers();
    await askStream('/x', 'q', h, {
      fetchImpl: vi
        .fn()
        .mockResolvedValue(
          sseResponse([sseFrame('sources', { sources: ['old.md', CITE[0], { title: 1 }] })]),
        ),
    });
    expect(h.onSources).toHaveBeenCalledWith(CITE);
  });

  it('maps a mid-stream error frame to its code plus the response trace id', async () => {
    const h = handlers();
    await askStream('/x', 'q', h, {
      fetchImpl: vi
        .fn()
        .mockResolvedValue(
          sseResponse([
            sseFrame('sources', { sources: CITE, count: 1 }),
            sseFrame('error', { error: 'ai_unavailable', error_kind: 'timeout' }),
          ]),
        ),
    });
    expect(h.onError).toHaveBeenCalledWith({ code: 'ai_unavailable', traceId: TRACE });
    expect(h.onDone).not.toHaveBeenCalled();
  });

  it('surfaces the api error code and trace id from a non-2xx before the stream', async () => {
    const h = handlers();
    await askStream('/x', 'q', h, {
      fetchImpl: vi
        .fn()
        .mockResolvedValue(
          Response.json(
            { error: 'ai_unavailable', trace_id: TRACE },
            { status: 503, headers: { 'x-trace-id': TRACE } },
          ),
        ),
    });
    expect(h.onError).toHaveBeenCalledWith({ code: 'ai_unavailable', traceId: TRACE });
  });

  it('falls back to bounded codes for bodies without the contract and for network failures', async () => {
    const h1 = handlers();
    await askStream('/x', 'q', h1, {
      fetchImpl: vi.fn().mockResolvedValue(new Response('nope', { status: 400 })),
    });
    expect(h1.onError).toHaveBeenCalledWith({ code: 'request_rejected', traceId: undefined });

    const h2 = handlers();
    await askStream('/x', 'q', h2, {
      fetchImpl: vi.fn().mockResolvedValue(new Response('down', { status: 502 })),
    });
    expect(h2.onError).toHaveBeenCalledWith({ code: 'upstream_error', traceId: undefined });

    const h3 = handlers();
    await askStream('/x', 'q', h3, { fetchImpl: vi.fn().mockRejectedValue(new Error('down')) });
    expect(h3.onError).toHaveBeenCalledWith({ code: 'network_error' });
  });

  it('skips malformed frames and finishes on stream end without a done frame', async () => {
    const h = handlers();
    await askStream('/x', 'q', h, {
      fetchImpl: vi
        .fn()
        .mockResolvedValue(
          sseResponse(['event: token\ndata: {broken\n\n', sseFrame('token', { text: 'ok' })]),
        ),
    });
    expect(h.onToken).toHaveBeenCalledWith('ok');
    expect(h.onDone).toHaveBeenCalledWith({ citations: [], inputTokens: null, outputTokens: null });
  });
});

describe('createConversation', () => {
  it('posts to the same-origin BFF and returns the id', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(Response.json({ id: 'c1', title: 'New conversation' }, { status: 201 }));
    await expect(createConversation(undefined, { fetchImpl })).resolves.toBe('c1');
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe('/api/v1/conversations');
    expect(JSON.parse(init.body)).toEqual({});
  });

  it('throws the api error code with the trace id', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { error: 'upstream_unreachable', trace_id: TRACE },
          { status: 502, headers: { 'x-trace-id': TRACE } },
        ),
      );
    await expect(createConversation(undefined, { fetchImpl })).rejects.toEqual({
      code: 'upstream_unreachable',
      traceId: TRACE,
    });
  });
});
