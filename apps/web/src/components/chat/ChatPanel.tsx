'use client';

import { useRef, useState } from 'react';
import {
  askStream,
  askStreamEndpoint,
  createConversation,
  type ChatError,
} from '@/lib/chat-client';
import { ChatThread } from './ChatThread';
import { MessageInput } from './MessageInput';
import type { ChatMessage } from './types';

interface Props {
  /** Continue this conversation; without one, the first question starts a new conversation. */
  conversationId?: string;
  title?: string;
  /** Called once a conversation exists (e.g. so a list can select it — roadmap web 2.1). */
  onConversation?: (id: string) => void;
}

let counter = 0;
const nextId = () => `m${++counter}`;

/**
 * A complete streaming chat surface: thread + input, wired to the conversation BFF routes
 * (same-origin only — the BFF rule). The first question creates the conversation
 * (`POST /api/v1/conversations`), then every question streams from
 * `/api/v1/conversations/{id}/ask/stream`; the api keeps the history.
 */
export function ChatPanel({ conversationId, title = 'Assistant', onConversation }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [activeId, setActiveId] = useState<string | undefined>(conversationId);
  const abortRef = useRef<AbortController | null>(null);

  function patchLast(patch: (m: ChatMessage) => ChatMessage) {
    setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? patch(m) : m)));
  }

  async function send(question: string) {
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: 'user', text: question },
      { id: nextId(), role: 'assistant', text: '', pending: true },
    ]);
    setBusy(true);
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    const fail = (error: ChatError) => patchLast((m) => ({ ...m, pending: false, error }));

    let id = activeId;
    if (!id) {
      try {
        id = await createConversation(undefined, { signal: abortRef.current.signal });
        setActiveId(id);
        onConversation?.(id);
      } catch (err) {
        fail(err as ChatError);
        setBusy(false);
        return;
      }
    }

    await askStream(
      askStreamEndpoint(id),
      question,
      {
        onSources: (citations) => patchLast((m) => ({ ...m, citations })),
        onToken: (text) => patchLast((m) => ({ ...m, text: m.text + text })),
        onDone: ({ citations }) =>
          patchLast((m) => ({
            ...m,
            pending: false,
            citations: citations.length ? citations : m.citations,
          })),
        onError: fail,
      },
      { signal: abortRef.current.signal },
    );
    setBusy(false);
  }

  return (
    <section className="mx-auto flex w-full max-w-3xl flex-col gap-4" aria-label={title}>
      <h1 className="text-xl font-semibold">{title}</h1>
      <div className="min-h-[16rem] rounded-2xl border border-neutral-200 bg-neutral-50 p-4 dark:border-neutral-800 dark:bg-neutral-950">
        <ChatThread messages={messages} />
      </div>
      <MessageInput onSubmit={send} disabled={busy} />
    </section>
  );
}
