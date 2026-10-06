import { withBff } from '@/lib/trace';
import { log } from '@/lib/logger';
import { isMockUpstream, mockJson } from '@/lib/mocks';
import { BodyTooLarge, errorJson, proxyJson, readBoundedJson } from '@/lib/conversations';
import { conversationListFixture, createdConversationFixture } from '@/mocks/conversations';

export const dynamic = 'force-dynamic';

const ROUTE = '/api/v1/conversations';

/**
 * POST /api/v1/conversations → api POST /v1/conversations (start a conversation).
 * Forwards only `{ title }` (a string, or nothing); the api applies the default and the
 * 200-character rule. Body capped at MAX_BODY_BYTES.
 */
export async function POST(req: Request): Promise<Response> {
  return withBff(
    req,
    async ({ traceId }) => {
      log('info', 'bff inbound', { route: ROUTE, method: 'POST' });
      let body: unknown;
      try {
        body = await readBoundedJson(req);
      } catch (err) {
        if (err instanceof BodyTooLarge) return errorJson('payload_too_large', 413, traceId);
        throw err;
      }
      if (
        body === null ||
        (body !== undefined && (typeof body !== 'object' || Array.isArray(body)))
      ) {
        return errorJson('validation_error', 422, traceId);
      }
      const title = (body as { title?: unknown } | undefined)?.title;
      if (title !== undefined && title !== null && typeof title !== 'string') {
        return errorJson('validation_error', 422, traceId);
      }
      if (isMockUpstream()) return mockJson(createdConversationFixture(title), traceId, 201);

      return proxyJson('/v1/conversations', traceId, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(typeof title === 'string' ? { title } : {}),
      });
    },
    { routeClass: ROUTE },
  );
}

/**
 * GET /api/v1/conversations?limit= → api GET /v1/conversations (newest first).
 * Only `limit` is forwarded, and only as 1–100; anything else in the query string is ignored.
 */
export async function GET(req: Request): Promise<Response> {
  return withBff(
    req,
    async ({ traceId }) => {
      log('info', 'bff inbound', { route: ROUTE, method: 'GET' });
      const raw = new URL(req.url).searchParams.get('limit');
      let query = '';
      if (raw !== null) {
        const limit = /^\d{1,3}$/.test(raw) ? Number(raw) : NaN;
        if (!(limit >= 1 && limit <= 100)) return errorJson('validation_error', 422, traceId);
        query = `?limit=${limit}`;
      }
      if (isMockUpstream()) return mockJson(conversationListFixture, traceId);
      return proxyJson(`/v1/conversations${query}`, traceId, { method: 'GET' });
    },
    { routeClass: ROUTE },
  );
}
