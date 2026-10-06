import { sseFrame } from '@/lib/sse';
import type { components } from '@/lib/api-types';

/**
 * Fixtures for `/api/v1/conversations…` (roadmap web 1.1), typed against the api contract
 * (`docs/reference/openapi.json` → `@/lib/api-types`). Served only with `MOCK_UPSTREAM=true`.
 */
type Schemas = components['schemas'];

export const MOCK_CONVERSATION_ID = '7f766eca-8bda-4dfb-8f54-c41f4e8726ef';

export const conversationListFixture: Schemas['ConversationList'] = {
  items: [
    {
      id: MOCK_CONVERSATION_ID,
      title: 'How does trace_id propagation work?',
      updated_at: '2026-10-06T10:12:00Z',
    },
    {
      id: '2b1c4d9e-0000-4a6f-9c3e-5d2b7a1e8f40',
      title: 'What is the error contract?',
      updated_at: '2026-10-06T09:40:00Z',
    },
    {
      id: '9d8e7f60-1111-4b2c-8d3e-4f5a6b7c8d9e',
      title: 'Onboarding',
      updated_at: '2026-10-05T16:05:00Z',
    },
  ],
  count: 3,
};

export const emptyConversationListFixture: Schemas['ConversationList'] = { items: [], count: 0 };

export const conversationDetailFixture: Schemas['ConversationDetail'] = {
  id: MOCK_CONVERSATION_ID,
  title: 'How does trace_id propagation work?',
  created_at: '2026-10-06T10:11:30Z',
  updated_at: '2026-10-06T10:12:00Z',
  messages: [
    {
      id: '0a1b2c3d-0000-4e5f-8a9b-0c1d2e3f4a5b',
      role: 'user',
      content: 'How does trace_id propagation work?',
      citations: null,
      created_at: '2026-10-06T10:11:40Z',
    },
    {
      id: '1b2c3d4e-0000-4f5a-9b0c-1d2e3f4a5b6c',
      role: 'assistant',
      content:
        'The trace id is minted at the edge when a request arrives without a valid one, forwarded unchanged on every hop and echoed on every response [1]. See the local setup for running both services [2].',
      citations: [
        { title: 'The `trace_id` contract', path: 'architecture/tracing.md' },
        { title: 'Getting started', path: 'getting-started.md' },
      ],
      created_at: '2026-10-06T10:12:00Z',
    },
  ],
};

/** `POST /v1/conversations` → 201, echoing the title the caller sent (or the default). */
export function createdConversationFixture(title?: string | null): Schemas['ConversationOut'] {
  return {
    id: '3c4d5e6f-2222-4a7b-8c9d-0e1f2a3b4c5d',
    title: title?.trim() || 'New conversation',
    created_at: '2026-10-06T10:15:00Z',
    updated_at: '2026-10-06T10:15:00Z',
  };
}

// ── ask (roadmap web 1.2) ────────────────────────────────────────────────────

const MOCK_ANSWER =
  'The trace id is minted at the edge and forwarded unchanged on every hop [1]. Run both services locally with just dev [2].';
const MOCK_CITATIONS: Schemas['Citation'][] = [
  { title: 'The `trace_id` contract', path: 'architecture/tracing.md' },
  { title: 'Getting started', path: 'getting-started.md' },
];

/** `POST /v1/conversations/{id}/ask` → 200. */
export const askFixture: Schemas['AskResponse'] = {
  message_id: '4d5e6f70-3333-4b8c-9d0e-1f2a3b4c5d6e',
  answer: MOCK_ANSWER,
  citations: MOCK_CITATIONS,
};

/**
 * Mock mode picks a scenario from a marker in the question, so the UI's error states can be
 * exercised without the api: `[mock:error]` → a mid-stream `error` frame, `[mock:503]` → a JSON
 * 503 before the first frame; anything else → a full answer.
 */
export type MockAskScenario = 'answer' | 'error' | '503';

export function mockAskScenario(question: string): MockAskScenario {
  if (question.includes('[mock:503]')) return '503';
  if (question.includes('[mock:error]')) return 'error';
  return 'answer';
}

/** The frames the api's `stream_answer()` emits (ADR-0015 shapes): sources → token* → done. */
export const askStreamFrames: string[] = [
  sseFrame('sources', { sources: MOCK_CITATIONS, count: 3 }),
  ...MOCK_ANSWER.split(' ').map((word) => sseFrame('token', { text: `${word} ` })),
  sseFrame('done', { sources: MOCK_CITATIONS, input_tokens: 812, output_tokens: 31 }),
];

/** A failure after the first frame: the stream ends with an `error` frame. */
export const askStreamErrorFrames: string[] = [
  sseFrame('sources', { sources: MOCK_CITATIONS, count: 3 }),
  sseFrame('token', { text: 'The trace id is ' }),
  sseFrame('error', { error: 'ai_unavailable', error_kind: 'timeout' }),
];
