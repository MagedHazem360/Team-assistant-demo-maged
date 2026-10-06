// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ChatThread } from './ChatThread';

describe('<ChatThread />', () => {
  afterEach(cleanup);

  it('shows the empty state', () => {
    render(<ChatThread messages={[]} />);
    expect(screen.getByTestId('chat-empty')).toHaveTextContent('Ask a question');
  });

  it('renders turns, citations as [n] title + path, pending and error states', () => {
    render(
      <ChatThread
        messages={[
          { id: '1', role: 'user', text: 'Which port?' },
          {
            id: '2',
            role: 'assistant',
            text: 'Port 8000 [1].',
            citations: [{ title: 'Ports', path: 'reference/ports.md' }],
            pending: true,
          },
          {
            id: '3',
            role: 'assistant',
            text: '',
            error: { code: 'ai_unavailable', traceId: '0eb01aaaa' },
          },
        ]}
      />,
    );
    const items = screen.getAllByRole('listitem').filter((li) => li.hasAttribute('data-role'));
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveAttribute('data-role', 'user');
    expect(screen.getByText('Port 8000 [1].')).toBeInTheDocument();
    const sources = screen.getByLabelText('Sources');
    expect(sources).toHaveTextContent('[1] Ports');
    expect(sources).toHaveTextContent('reference/ports.md');
    expect(screen.getByLabelText('Streaming')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'error: ai_unavailable · trace_id: 0eb01aaaa',
    );
  });

  it('renders citation titles and paths as plain text, never markup or links', () => {
    const { container } = render(
      <ChatThread
        messages={[
          {
            id: '1',
            role: 'assistant',
            text: 'x',
            citations: [{ title: '<img src=x onerror=alert(1)>', path: 'javascript:alert(1)' }],
          },
        ]}
      />,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('a')).toBeNull();
    expect(screen.getByLabelText('Sources')).toHaveTextContent('<img src=x onerror=alert(1)>');
  });
});
