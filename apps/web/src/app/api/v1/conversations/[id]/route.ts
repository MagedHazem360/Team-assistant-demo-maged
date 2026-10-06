import { withBff } from '@/lib/trace';
import { log } from '@/lib/logger';
import { isMockUpstream, mockJson } from '@/lib/mocks';
import { errorJson, proxyJson, UUID_RE } from '@/lib/conversations';
import { conversationDetailFixture, MOCK_CONVERSATION_ID } from '@/mocks/conversations';

export const dynamic = 'force-dynamic';

/** The route pattern, never the id — metric attributes stay bounded (rule 60). */
const ROUTE = '/api/v1/conversations/[id]';

/**
 * GET /api/v1/conversations/{id} → api GET /v1/conversations/{id} (with its messages).
 * The id is checked as a UUID before anything is forwarded, so a client can never steer the
 * upstream path.
 */
export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  return withBff(
    req,
    async ({ traceId }) => {
      const { id } = await params;
      log('info', 'bff inbound', { route: ROUTE, method: 'GET' });
      if (!UUID_RE.test(id)) return errorJson('validation_error', 422, traceId);
      if (isMockUpstream()) {
        return id.toLowerCase() === MOCK_CONVERSATION_ID
          ? mockJson(conversationDetailFixture, traceId)
          : mockJson({ error: 'not_found', trace_id: traceId }, traceId, 404);
      }
      return proxyJson(`/v1/conversations/${id.toLowerCase()}`, traceId, { method: 'GET' });
    },
    { routeClass: ROUTE },
  );
}
