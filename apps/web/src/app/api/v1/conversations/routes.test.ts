import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GET as list, POST as create } from '@/app/api/v1/conversations/route';
import { GET as detail } from '@/app/api/v1/conversations/[id]/route';
import { MAX_BODY_BYTES } from '@/lib/conversations';
import { MOCK_HEADER } from '@/lib/mocks';
import { TRACE_HEADER } from '@/lib/trace';
import {
  conversationDetailFixture,
  conversationListFixture,
  MOCK_CONVERSATION_ID,
} from '@/mocks/conversations';

const INBOUND = `0eb00${'e'.repeat(27)}`;
const ID = '11111111-2222-3333-4444-555555555555';

function req(method: string, path: string, body?: string, headers: Record<string, string> = {}) {
  return new Request(`http://web${path}`, {
    method,
    headers: { 'content-type': 'application/json', [TRACE_HEADER]: INBOUND, ...headers },
    body,
  });
}

const params = (id: string) => ({ params: Promise.resolve({ id }) });

async function expectError(res: Response, status: number, error: string) {
  expect(res.status).toBe(status);
  expect(await res.json()).toEqual({ error, trace_id: INBOUND });
  expect(res.headers.get(TRACE_HEADER)).toBe(INBOUND);
}

describe('conversation BFF routes (/api/v1/conversations…)', () => {
  beforeEach(() => {
    process.env.API_BASE_URL = 'http://api:8000';
    delete process.env.MOCK_UPSTREAM;
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete process.env.MOCK_UPSTREAM;
  });

  describe('POST /api/v1/conversations', () => {
    it('forwards only the title, with the same x-trace-id and a bounded timeout', async () => {
      const created = { id: ID, title: 'Trace', created_at: 'x', updated_at: 'x' };
      const spy = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(Response.json(created, { status: 201 }));
      const res = await create(
        req('POST', '/api/v1/conversations', JSON.stringify({ title: 'Trace', extra: 'drop me' })),
      );
      expect(res.status).toBe(201);
      await expect(res.json()).resolves.toEqual(created);
      const [url, init] = spy.mock.calls[0];
      expect(url).toBe('http://api:8000/v1/conversations');
      expect(init?.method).toBe('POST');
      expect(JSON.parse(String(init?.body))).toEqual({ title: 'Trace' });
      expect(new Headers(init?.headers).get(TRACE_HEADER)).toBe(INBOUND);
      expect(init?.signal).toBeInstanceOf(AbortSignal);
      expect(res.headers.get(TRACE_HEADER)).toBe(INBOUND);
    });

    it('sends an empty object when there is no body or no title', async () => {
      const spy = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(Response.json({}, { status: 201 }));
      await create(req('POST', '/api/v1/conversations'));
      await create(req('POST', '/api/v1/conversations', '{}'));
      expect(spy.mock.calls.map(([, init]) => JSON.parse(String(init?.body)))).toEqual([{}, {}]);
    });

    it('passes the api error contract through unchanged', async () => {
      const apiError = { error: 'validation_error', trace_id: INBOUND };
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(apiError, { status: 422 }));
      await expectError(
        await create(
          req('POST', '/api/v1/conversations', JSON.stringify({ title: 'x'.repeat(201) })),
        ),
        422,
        'validation_error',
      );
    });

    it.each([
      ['malformed JSON', '{not json'],
      ['an array', '[]'],
      ['a non-string title', JSON.stringify({ title: 42 })],
    ])('refuses %s with 422 without calling the api', async (_label, body) => {
      const spy = vi.spyOn(globalThis, 'fetch');
      await expectError(
        await create(req('POST', '/api/v1/conversations', body)),
        422,
        'validation_error',
      );
      expect(spy).not.toHaveBeenCalled();
    });

    it('refuses a body over the cap with 413 without calling the api', async () => {
      const spy = vi.spyOn(globalThis, 'fetch');
      const big = JSON.stringify({ title: 'x'.repeat(MAX_BODY_BYTES) });
      await expectError(
        await create(req('POST', '/api/v1/conversations', big)),
        413,
        'payload_too_large',
      );
      expect(spy).not.toHaveBeenCalled();
    });

    it('answers 502 with the trace id when the api is unreachable', async () => {
      vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('fetch failed'));
      const res = await create(req('POST', '/api/v1/conversations', '{}'));
      expect(res.status).toBe(502);
      expect(await res.json()).toEqual({
        error: 'upstream_unreachable',
        target: 'api',
        trace_id: INBOUND,
      });
    });

    it('serves a typed fixture in mock mode, marked as a mock', async () => {
      process.env.MOCK_UPSTREAM = 'true';
      const spy = vi.spyOn(globalThis, 'fetch');
      const res = await create(
        req('POST', '/api/v1/conversations', JSON.stringify({ title: ' Hi ' })),
      );
      expect(res.status).toBe(201);
      expect(res.headers.get(MOCK_HEADER)).toBe('true');
      expect((await res.json()).title).toBe('Hi');
      expect(spy).not.toHaveBeenCalled();
    });
  });

  describe('GET /api/v1/conversations', () => {
    it('forwards only a valid limit and ignores other query parameters', async () => {
      const spy = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(Response.json({ items: [], count: 0 }));
      await list(req('GET', '/api/v1/conversations?limit=5&evil=../../x'));
      await list(req('GET', '/api/v1/conversations'));
      expect(spy.mock.calls.map(([url]) => url)).toEqual([
        'http://api:8000/v1/conversations?limit=5',
        'http://api:8000/v1/conversations',
      ]);
    });

    it.each(['0', '101', 'abc', '5.5', '-1'])('refuses limit=%s with 422', async (limit) => {
      const spy = vi.spyOn(globalThis, 'fetch');
      await expectError(
        await list(req('GET', `/api/v1/conversations?limit=${limit}`)),
        422,
        'validation_error',
      );
      expect(spy).not.toHaveBeenCalled();
    });

    it('serves the list fixture in mock mode', async () => {
      process.env.MOCK_UPSTREAM = 'true';
      const res = await list(req('GET', '/api/v1/conversations'));
      expect(res.headers.get(MOCK_HEADER)).toBe('true');
      await expect(res.json()).resolves.toEqual(conversationListFixture);
    });
  });

  describe('GET /api/v1/conversations/{id}', () => {
    it('forwards a UUID id (lower-cased) and passes the answer through', async () => {
      const spy = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(Response.json(conversationDetailFixture));
      const res = await detail(
        req('GET', `/api/v1/conversations/${ID.toUpperCase()}`),
        params(ID.toUpperCase()),
      );
      expect(res.status).toBe(200);
      expect(spy.mock.calls[0][0]).toBe(`http://api:8000/v1/conversations/${ID}`);
      await expect(res.json()).resolves.toEqual(conversationDetailFixture);
    });

    it('passes the api 404 through', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue(
        Response.json({ error: 'not_found', trace_id: INBOUND }, { status: 404 }),
      );
      await expectError(
        await detail(req('GET', `/api/v1/conversations/${ID}`), params(ID)),
        404,
        'not_found',
      );
    });

    it.each(['not-a-uuid', '..%2F..%2Fhealth', `${ID}/ask`])(
      'refuses id %s with 422 without calling the api',
      async (id) => {
        const spy = vi.spyOn(globalThis, 'fetch');
        await expectError(
          await detail(req('GET', '/api/v1/conversations/x'), params(id)),
          422,
          'validation_error',
        );
        expect(spy).not.toHaveBeenCalled();
      },
    );

    it('serves the detail fixture for the mock id and 404 for any other in mock mode', async () => {
      process.env.MOCK_UPSTREAM = 'true';
      const found = await detail(
        req('GET', '/api/v1/conversations/x'),
        params(MOCK_CONVERSATION_ID),
      );
      expect(found.headers.get(MOCK_HEADER)).toBe('true');
      await expect(found.json()).resolves.toEqual(conversationDetailFixture);
      const missing = await detail(req('GET', '/api/v1/conversations/x'), params(ID));
      await expectError(missing, 404, 'not_found');
    });
  });
});
