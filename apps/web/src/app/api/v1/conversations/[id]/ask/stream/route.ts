import { withBff } from '@/lib/trace';
import { log } from '@/lib/logger';
import { isMockUpstream, mockJson, mockSse } from '@/lib/mocks';
import { proxyStream, streamUpstream } from '@/lib/stream';
import { apiBase, BodyTooLarge, errorJson, readQuestion, UUID_RE } from '@/lib/conversations';
import { askStreamErrorFrames, askStreamFrames, mockAskScenario } from '@/mocks/conversations';

export const dynamic = 'force-dynamic';

/** The route pattern, never the id — metric attributes stay bounded (rule 60). */
const ROUTE = '/api/v1/conversations/[id]/ask/stream';

/**
 * POST /api/v1/conversations/{id}/ask/stream → api POST /v1/conversations/{id}/ask/stream (SSE).
 *
 * The api's frames are passed through unchanged (`sources` → `token`* → `done`, or a final
 * `error` frame — ADR-0015 shapes). An api error **before** the stream starts (404, 413, 422,
 * 503) is its JSON `{error, trace_id}` with its own status, passed through as-is so the browser
 * can show the code; an unreachable api is the BFF's `502 upstream_unreachable`.
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
        const scenario = mockAskScenario(question);
        if (scenario === '503') {
          return mockJson({ error: 'ai_unavailable', trace_id: traceId }, traceId, 503);
        }
        return mockSse(scenario === 'error' ? askStreamErrorFrames : askStreamFrames, traceId);
      }

      const url = `${apiBase()}/v1/conversations/${id.toLowerCase()}/ask/stream`;
      try {
        // streamUpstream: whole-answer bound STREAM_TIMEOUT_MS (5 min), x-trace-id forwarded.
        const upstream = await streamUpstream(url, traceId, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ question }),
        });
        const isStream = (upstream.headers.get('content-type') ?? '').includes('text/event-stream');
        if (upstream.ok && upstream.body && isStream) return proxyStream(upstream, traceId);
        // Before the first frame the api answers with its JSON error contract: pass it through.
        const data: unknown = await upstream.json().catch(() => null);
        log('info', 'bff upstream stream refused', { target: 'api', status: upstream.status });
        return data
          ? Response.json(data, { status: upstream.status })
          : errorJson('bad_gateway', 502, traceId);
      } catch (err) {
        const kind = err instanceof Error ? err.name : 'Error';
        log('error', 'bff upstream failed', { target: 'api', error: kind });
        return Response.json(
          { error: 'upstream_unreachable', target: 'api', trace_id: traceId },
          { status: 502 },
        );
      }
    },
    { routeClass: ROUTE },
  );
}
