import type { components } from '@/lib/api-types';
import { readSseStream } from '@/lib/sse';

/**
 * Browser-side client for the chat (same-origin BFF only — the BFF rule). Framework-free so it
 * is unit-tested in the node environment; components wrap it.
 *
 * Frame shapes are the api's (ADR-0015): `sources`/`done` carry `[{title, path}]`, an `error`
 * frame carries `{error, error_kind}`. Errors reach the UI as `{code, traceId}` — a bounded code
 * plus the request's trace id, never upstream text.
 */
export type Citation = components['schemas']['Citation'];

export interface ChatError {
  /** A bounded code: the api's (`ai_unavailable`, `not_found`, …) or the client's own. */
  code: string;
  /** The request's trace id (`x-trace-id`), when known — what to paste into Log Analytics. */
  traceId?: string;
}

export interface StreamHandlers {
  onSources?: (citations: Citation[]) => void;
  onToken: (text: string) => void;
  onDone?: (info: {
    citations: Citation[];
    inputTokens?: number | null;
    outputTokens?: number | null;
  }) => void;
  onError: (error: ChatError) => void;
}

export interface ClientOptions {
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

const TRACE_HEADER = 'x-trace-id';

function citationsOf(value: unknown): Citation[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (c): c is Citation =>
      !!c && typeof c === 'object' && typeof c.title === 'string' && typeof c.path === 'string',
  );
}

/** A non-2xx response as a ChatError: the api's `{error, trace_id}` body, else a fallback. */
async function errorFrom(res: Response): Promise<ChatError> {
  let body: { error?: unknown; trace_id?: unknown } | null = null;
  try {
    body = (await res.json()) as { error?: unknown; trace_id?: unknown };
  } catch {
    body = null;
  }
  const code =
    typeof body?.error === 'string'
      ? body.error
      : res.status >= 500
        ? 'upstream_error'
        : 'request_rejected';
  const traceId =
    res.headers.get(TRACE_HEADER) ??
    (typeof body?.trace_id === 'string' ? body.trace_id : undefined);
  return { code, traceId };
}

/** `POST /api/v1/conversations` → the new conversation's id (throws a ChatError on failure). */
export async function createConversation(
  title?: string,
  options: ClientOptions = {},
): Promise<string> {
  const fetchImpl = options.fetchImpl ?? fetch;
  let res: Response;
  try {
    res = await fetchImpl('/api/v1/conversations', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(title ? { title } : {}),
      signal: options.signal,
    });
  } catch (err) {
    throw { code: (err as Error)?.name === 'AbortError' ? 'aborted' : 'network_error' };
  }
  if (!res.ok) throw await errorFrom(res);
  const { id } = (await res.json()) as { id: string };
  return id;
}

/** The same-origin SSE route for a conversation. */
export function askStreamEndpoint(conversationId: string): string {
  return `/api/v1/conversations/${encodeURIComponent(conversationId)}/ask/stream`;
}

/**
 * POST `{ question }` to a same-origin SSE route and dispatch its events.
 * Resolves when the stream ends; every failure path calls `onError` with a bounded code.
 */
export async function askStream(
  endpoint: string,
  question: string,
  handlers: StreamHandlers,
  options: ClientOptions = {},
): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch;
  let res: Response;
  try {
    res = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify({ question }),
      signal: options.signal,
    });
  } catch (err) {
    handlers.onError({ code: (err as Error)?.name === 'AbortError' ? 'aborted' : 'network_error' });
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError(await errorFrom(res));
    return;
  }
  const traceId = res.headers.get(TRACE_HEADER) ?? undefined;
  let citations: Citation[] = [];
  try {
    for await (const event of readSseStream(res.body)) {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        continue; // a malformed frame is skipped, never fatal
      }
      switch (event.event) {
        case 'sources':
          citations = citationsOf(data.sources);
          handlers.onSources?.(citations);
          break;
        case 'token':
          if (typeof data.text === 'string') handlers.onToken(data.text);
          break;
        case 'done':
          handlers.onDone?.({
            citations: Array.isArray(data.sources) ? citationsOf(data.sources) : citations,
            inputTokens: (data.input_tokens as number | null | undefined) ?? null,
            outputTokens: (data.output_tokens as number | null | undefined) ?? null,
          });
          return;
        case 'error':
          handlers.onError({
            code: typeof data.error === 'string' ? data.error : 'error',
            traceId,
          });
          return;
        default:
          break;
      }
    }
    // Stream ended without a terminal frame.
    handlers.onDone?.({ citations, inputTokens: null, outputTokens: null });
  } catch (err) {
    handlers.onError({
      code: (err as Error)?.name === 'AbortError' ? 'aborted' : 'stream_error',
      traceId,
    });
  }
}
