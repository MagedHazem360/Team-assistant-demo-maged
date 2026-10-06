import { log } from '@/lib/logger';
import { fetchUpstream } from '@/lib/trace';

/**
 * Shared plumbing for the `/api/v1/conversations…` BFF routes (roadmap web 1.1).
 *
 * The BFF validates and shapes what it forwards (rule 50): only known fields, a UUID-checked
 * `{id}`, a bounded `limit`, and a capped request body — never an arbitrary client query string
 * or path. The api stays the source of truth for the rules; these checks just stop junk at the
 * edge. Titles and message content are user text: they are forwarded, never logged.
 */

/**
 * The dev Azure SQL database is serverless and can take ~30 s to wake (api 1.1 notes), longer
 * than fetchUpstream's 10 s default — bound these calls explicitly.
 */
export const CONVERSATIONS_TIMEOUT_MS = 35_000;

/** Bodies larger than this are refused before they are read into memory (create: `{title}`). */
export const MAX_BODY_BYTES = 4_096;

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The api error contract shape, minted at the BFF for requests it refuses itself. */
export function errorJson(error: string, status: number, traceId: string): Response {
  return Response.json({ error, trace_id: traceId }, { status });
}

export function apiBase(): string {
  return process.env.API_BASE_URL ?? 'http://localhost:8000';
}

/**
 * Read a JSON body of at most `MAX_BODY_BYTES`. Returns `undefined` for an empty body, `null` for
 * malformed JSON, and throws `BodyTooLarge` when the cap is exceeded.
 */
export class BodyTooLarge extends Error {}

export async function readBoundedJson(req: Request): Promise<unknown> {
  const declared = Number(req.headers.get('content-length') ?? '0');
  if (declared > MAX_BODY_BYTES) throw new BodyTooLarge();
  const text = await req.text();
  if (new TextEncoder().encode(text).length > MAX_BODY_BYTES) throw new BodyTooLarge();
  if (!text.trim()) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * Call the api and pass its JSON answer through unchanged — success **and** the api's own
 * `{error, trace_id}` errors keep their status. An unreachable api is the BFF's `502`.
 */
export async function proxyJson(
  path: string,
  traceId: string,
  init: RequestInit = {},
): Promise<Response> {
  const url = `${apiBase()}${path}`;
  try {
    const res = await fetchUpstream(url, traceId, {
      ...init,
      signal: init.signal ?? AbortSignal.timeout(CONVERSATIONS_TIMEOUT_MS),
    });
    const data: unknown = await res.json();
    log('info', 'bff upstream ok', { target: 'api', status: res.status });
    return Response.json(data, { status: res.status });
  } catch (err) {
    // The error *kind* only: an upstream message could echo request content.
    const kind = err instanceof Error ? err.name : 'Error';
    log('error', 'bff upstream failed', { target: 'api', error: kind });
    return Response.json(
      { error: 'upstream_unreachable', target: 'api', trace_id: traceId },
      { status: 502 },
    );
  }
}
