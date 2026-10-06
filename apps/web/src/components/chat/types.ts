import type { ChatError, Citation } from '@/lib/chat-client';

export type ChatRole = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  /** Documents the assistant cited, in marker order: `[n]` is `citations[n-1]` (ADR-0015). */
  citations?: Citation[];
  /** Still streaming. */
  pending?: boolean;
  /** A bounded error code plus the trace id (never raw exception text). */
  error?: ChatError;
}
