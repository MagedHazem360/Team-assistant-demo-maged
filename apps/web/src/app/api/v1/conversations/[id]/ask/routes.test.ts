import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { POST as ask } from '@/app/api/v1/conversations/[id]/ask/route';
import { POST as askStream } from '@/app/api/v1/conversations/[id]/ask/stream/route';
import { ASK_TIMEOUT_MS, MAX_ASK_BODY_BYTES } from '@/lib/conversations';
import { MOCK_HEADER } from '@/lib/mocks';
import { sseFrame, sseStreamFromFrames } from '@/lib/sse';
import { TRACE_HEADER } from '@/lib/trace';
import { askFixture } from '@/mocks/conversations';

const INBOUND = `0eb00${'f'.repeat(27)}`;
const ID = '11111111-2222-3333-4444-555555555555';
const CITE = [{ title: 'Ports', path: 'reference/ports.md' }];

function req(body: unknown, path = `/api/v1/conversations/${ID}/ask`) {
  return new Request(`http://web${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', [TRACE_HEADER]: INBOUND },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}
const params = (id = ID) => ({ params: Promise.resolve({ id }) });

async function expectError(res: Response, status: number, error: string) {
  expect(res.status).toBe(status);
  expect(await res.json()).toEqual({ error, trace_id: INBOUND });
  expect(res.headers.get(TRACE_HEADER)).toBe(INBOUND);
}

function sseUpstream(frames: string[]) {
  return new Response(sseStreamFromFrames(frames), {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

describe('ask BFF routes (/api/v1/conversations/{id}/ask[/stream])', () => {
  beforeEach(() => {
    process.env.API_BASE_URL = 'http://api:8000';
    delete process.env.MOCK_UPSTREAM;
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete process.env.MOCK_UPSTREAM;
  });

  describe.each([
    ['ask', ask],
    ['ask/stream', askStream],
  ])('shared validation — %s', (_name, handler) => {
    it.each([
      ['an empty question', { question: '   ' }],
      ['no question', {}],
      ['a non-string question', { question: 42 }],
      ['malformed JSON', '{oops'],
    ])('refuses %s with 422 without calling the api', async (_label, body) => {
      const spy = vi.spyOn(globalThis, 'fetch');
      await expectError(await handler(req(body), params()), 422, 'validation_error');
      expect(spy).not.toHaveBeenCalled();
    });

    it('refuses a malformed id with 422', async () => {
      const spy = vi.spyOn(globalThis, 'fetch');
      await expectError(
        await handler(req({ question: 'q' }), params('../health')),
        422,
        'validation_error',
      );
      expect(spy).not.toHaveBeenCalled();
    });

    it('refuses a body over the cap with 413', async () => {
      const spy = vi.spyOn(globalThis, 'fetch');
      const big = { question: 'x'.repeat(MAX_ASK_BODY_BYTES) };
      await expectError(await handler(req(big), params()), 413, 'payload_too_large');
      expect(spy).not.toHaveBeenCalled();
    });
  });

  describe('POST …/ask (JSON)', () => {
    it('forwards only the trimmed question with the trace id and a long timeout', async () => {
      const answer = { message_id: 'm1', answer: 'a [1]', citations: CITE };
      const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(answer));
      const timeout = vi.spyOn(AbortSignal, 'timeout');
      const res = await ask(
        req({ question: '  hi  ', extra: 'drop me' }),
        params(ID.toUpperCase()),
      );

      expect(res.status).toBe(200);
      await expect(res.json()).resolves.toEqual(answer);
      const [url, init] = spy.mock.calls[0];
      expect(url).toBe(`http://api:8000/v1/conversations/${ID}/ask`);
      expect(JSON.parse(String(init?.body))).toEqual({ question: 'hi' });
      expect(new Headers(init?.headers).get(TRACE_HEADER)).toBe(INBOUND);
      expect(timeout).toHaveBeenCalledWith(ASK_TIMEOUT_MS);
      expect(ASK_TIMEOUT_MS).toBeGreaterThanOrEqual(60_000);
    });

    it('passes the api errors through (404, 413, 503)', async () => {
      for (const [status, error] of [
        [404, 'not_found'],
        [413, 'payload_too_large'],
        [503, 'ai_unavailable'],
      ] as const) {
        vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
          Response.json({ error, trace_id: INBOUND }, { status }),
        );
        await expectError(await ask(req({ question: 'q' }), params()), status, error);
      }
    });

    it('serves the fixture in mock mode, and a 503 for the [mock:503] marker', async () => {
      process.env.MOCK_UPSTREAM = 'true';
      const ok = await ask(req({ question: 'hi' }), params());
      expect(ok.headers.get(MOCK_HEADER)).toBe('true');
      await expect(ok.json()).resolves.toEqual(askFixture);
      await expectError(
        await ask(req({ question: 'x [mock:503]' }), params()),
        503,
        'ai_unavailable',
      );
    });
  });

  describe('POST …/ask/stream (SSE)', () => {
    it('passes the api frames through unchanged, with the trace id and no buffering', async () => {
      const frames = [
        sseFrame('sources', { sources: CITE, count: 1 }),
        sseFrame('token', { text: 'Port 8000 [1]' }),
        sseFrame('done', { sources: CITE, input_tokens: 1, output_tokens: 2 }),
      ];
      const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(sseUpstream(frames));
      const res = await askStream(req({ question: 'hi' }), params());

      expect(res.status).toBe(200);
      expect(res.headers.get('content-type')).toContain('text/event-stream');
      expect(res.headers.get('x-accel-buffering')).toBe('no');
      expect(res.headers.get(TRACE_HEADER)).toBe(INBOUND);
      expect(await res.text()).toBe(frames.join(''));
      const [url, init] = spy.mock.calls[0];
      expect(url).toBe(`http://api:8000/v1/conversations/${ID}/ask/stream`);
      expect(JSON.parse(String(init?.body))).toEqual({ question: 'hi' });
      expect(new Headers(init?.headers).get(TRACE_HEADER)).toBe(INBOUND);
    });

    it('passes an api JSON error before the first frame through with its status', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        Response.json({ error: 'ai_unavailable', trace_id: INBOUND }, { status: 503 }),
      );
      await expectError(await askStream(req({ question: 'q' }), params()), 503, 'ai_unavailable');
    });

    it('answers 502 upstream_unreachable when the api cannot be reached', async () => {
      vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('fetch failed'));
      const res = await askStream(req({ question: 'q' }), params());
      expect(res.status).toBe(502);
      expect(await res.json()).toEqual({
        error: 'upstream_unreachable',
        target: 'api',
        trace_id: INBOUND,
      });
    });

    it('mock mode: a full answer, a mid-stream error, and a 503 before the first frame', async () => {
      process.env.MOCK_UPSTREAM = 'true';
      const full = await askStream(req({ question: 'hi' }), params());
      expect(full.headers.get(MOCK_HEADER)).toBe('true');
      const fullText = await full.text();
      expect(fullText).toContain('event: sources');
      expect(fullText).toContain('"path":"architecture/tracing.md"');
      expect(fullText.trim().split('\n\n').at(-1)).toContain('event: done');

      const broken = await (await askStream(req({ question: 'x [mock:error]' }), params())).text();
      expect(broken.trim().split('\n\n').at(-1)).toBe(
        sseFrame('error', { error: 'ai_unavailable', error_kind: 'timeout' }).trim(),
      );

      await expectError(
        await askStream(req({ question: 'x [mock:503]' }), params()),
        503,
        'ai_unavailable',
      );
    });
  });
});
