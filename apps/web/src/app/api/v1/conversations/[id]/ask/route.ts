import { withBff } from '@/lib/trace';
import { log } from '@/lib/logger';
import { isMockUpstream, mockJson } from '@/lib/mocks';
import {
  ASK_TIMEOUT_MS,
  BodyTooLarge,
  errorJson,
  proxyJson,
  readQuestion,
  UUID_RE,
} from '@/lib/conversations';
import { askFixture, mockAskScenario } from '@/mocks/conversations';

export const dynamic = 'force-dynamic';

/** The route pattern, never the id — metric attributes stay bounded (rule 60). */
const ROUTE = '/api/v1/conversations/[id]/ask';

/**
 * POST /api/v1/conversations/{id}/ask → api POST /v1/conversations/{id}/ask (JSON answer).
 * Forwards only `{ question }`; the id is UUID-checked; bounded at ASK_TIMEOUT_MS (a model
 * answer routinely exceeds fetchUpstream's 10 s default — rule 30).
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  return withBff(
    req,
    async ({ traceId }) => {
      const { id } = await params;
      log('info', 'bff inbound', { route: ROUTE, method: 'POST' });
      if (!UUID_RE.test(id)) return errorJson('validation_error', 422, traceId);
      let question: string | undefined;
      try {
        question = await readQuestion(req);
      } catch (err) {
        if (err instanceof BodyTooLarge) return errorJson('payload_too_large', 413, traceId);
        throw err;
      }
      if (!question) return errorJson('validation_error', 422, traceId);

      if (isMockUpstream()) {
        return mockAskScenario(question) === 'answer'
          ? mockJson(askFixture, traceId)
          : mockJson({ error: 'ai_unavailable', trace_id: traceId }, traceId, 503);
      }
      return proxyJson(`/v1/conversations/${id.toLowerCase()}/ask`, traceId, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question }),
        signal: AbortSignal.timeout(ASK_TIMEOUT_MS),
      });
    },
    { routeClass: ROUTE },
  );
}
