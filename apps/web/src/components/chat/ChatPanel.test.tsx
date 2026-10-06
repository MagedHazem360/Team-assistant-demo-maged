// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { sseFrame, sseStreamFromFrames } from '@/lib/sse';
import { ChatPanel } from './ChatPanel';

const CONV = '11111111-2222-3333-4444-555555555555';
const CITE = [{ title: 'Ports', path: 'reference/ports.md' }];

function stream(frames: string[]) {
  return new Response(sseStreamFromFrames(frames), {
    status: 200,
    headers: { 'content-type': 'text/event-stream', 'x-trace-id': `0eb00${'b'.repeat(27)}` },
  });
}

const answerFrames = [
  sseFrame('sources', { sources: CITE, count: 1 }),
  sseFrame('token', { text: 'Port ' }),
  sseFrame('token', { text: '8000 [1]' }),
  sseFrame('done', { sources: CITE }),
];

function calls() {
  return (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls;
}

describe('<ChatPanel />', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('creates a conversation on the first question, then streams the answer with citations', async () => {
    const onConversation = vi.fn();
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json({ id: CONV }, { status: 201 }))
      .mockResolvedValueOnce(stream(answerFrames))
      .mockResolvedValueOnce(stream(answerFrames));
    const user = userEvent.setup();
    render(<ChatPanel onConversation={onConversation} />);

    await user.type(screen.getByLabelText('Message'), 'which port?{Enter}');
    await waitFor(() => expect(screen.getByText('Port 8000 [1]')).toBeInTheDocument());
    expect(screen.getByLabelText('Sources')).toHaveTextContent('[1] Ports');
    expect(screen.getByLabelText('Sources')).toHaveTextContent('reference/ports.md');
    await waitFor(() => expect(screen.queryByLabelText('Streaming')).not.toBeInTheDocument());
    expect(onConversation).toHaveBeenCalledWith(CONV);

    // A follow-up continues the same conversation — no second create.
    await user.type(screen.getByLabelText('Message'), 'and the web?{Enter}');
    await waitFor(() => expect(calls()).toHaveLength(3));
    expect(calls().map(([url]) => url)).toEqual([
      '/api/v1/conversations',
      `/api/v1/conversations/${CONV}/ask/stream`,
      `/api/v1/conversations/${CONV}/ask/stream`,
    ]); // same-origin only
  });

  it('continues a given conversation without creating one', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(stream(answerFrames));
    const user = userEvent.setup();
    render(<ChatPanel conversationId={CONV} />);
    await user.type(screen.getByLabelText('Message'), 'q{Enter}');
    await waitFor(() => expect(screen.getByText('Port 8000 [1]')).toBeInTheDocument());
    expect(calls()[0][0]).toBe(`/api/v1/conversations/${CONV}/ask/stream`);
  });

  it('shows the api error code and trace id when answering fails', async () => {
    const trace = `0c700${'c'.repeat(27)}`;
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      Response.json(
        { error: 'ai_unavailable', trace_id: trace },
        { status: 503, headers: { 'x-trace-id': trace } },
      ),
    );
    const user = userEvent.setup();
    render(<ChatPanel conversationId={CONV} />);
    await user.type(screen.getByLabelText('Message'), 'q{Enter}');
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        `error: ai_unavailable · trace_id: ${trace}`,
      ),
    );
  });

  it('shows the error when the conversation cannot be created', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      Response.json({ error: 'upstream_unreachable', trace_id: 't1' }, { status: 502 }),
    );
    const user = userEvent.setup();
    render(<ChatPanel />);
    await user.type(screen.getByLabelText('Message'), 'q{Enter}');
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('upstream_unreachable'),
    );
    expect(calls()).toHaveLength(1);
  });
});
