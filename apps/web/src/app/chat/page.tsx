import { ChatPanel } from '@/components/chat/ChatPanel';

/**
 * The chat page. It talks only to the same-origin BFF: the first question creates a
 * conversation (`/api/v1/conversations`), then answers stream from
 * `/api/v1/conversations/{id}/ask/stream` with their citations. With `MOCK_UPSTREAM=true` those
 * routes serve the fixtures in `src/mocks/conversations.ts`. The conversation list and the
 * two-column layout arrive with roadmap web 2.1/2.2.
 */
export default function ChatPage() {
  return (
    <main className="px-4 py-8">
      <ChatPanel title="Team Assistant" />
      <p className="mx-auto mt-4 max-w-3xl text-xs text-neutral-500">
        Answers come from the team&apos;s documents. Conversations are visible to everyone using
        this app — don&apos;t paste personal or confidential data.
      </p>
    </main>
  );
}
