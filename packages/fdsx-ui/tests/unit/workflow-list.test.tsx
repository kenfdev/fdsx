import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { WorkflowList } from '../../src/client/components/WorkflowList.js';

const mockWorkflows = [
  { name: 'Zebra', filePath: '/z.yaml', relativePath: 'z.yaml' },
  { name: 'Alpha', filePath: '/a.yaml', relativePath: 'a.yaml' },
  { name: 'Middle', filePath: '/m.yaml', relativePath: 'm.yaml' },
];

describe('WorkflowList', () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows loading state initially', () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockWorkflows,
    });
    const onSelect = vi.fn();
    render(<WorkflowList selectedWorkflow={null} onSelect={onSelect} />);

    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('fetches and renders workflows in alphabetical order', async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockWorkflows,
    });
    const onSelect = vi.fn();
    render(<WorkflowList selectedWorkflow={null} onSelect={onSelect} />);

    await waitFor(() => {
      expect(screen.getByText('a.yaml')).toBeInTheDocument();
    });

    const items = screen.getAllByRole('button');
    expect(items[0]).toHaveTextContent('Alpha');
    expect(items[1]).toHaveTextContent('Middle');
    expect(items[2]).toHaveTextContent('Zebra');
  });

  it('calls onSelect when a workflow is clicked', async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => mockWorkflows,
    });
    const onSelect = vi.fn();
    render(<WorkflowList selectedWorkflow={null} onSelect={onSelect} />);

    await waitFor(() => screen.getByText('Alpha'));
    fireEvent.click(screen.getByText('Alpha'));

    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Alpha', relativePath: 'a.yaml' }),
    );
  });

  it('highlights the active workflow', async () => {
    const active = { name: 'Alpha', filePath: '/a.yaml', relativePath: 'a.yaml' };
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => [active],
    });
    const onSelect = vi.fn();
    render(<WorkflowList selectedWorkflow={active} onSelect={onSelect} />);

    await waitFor(() => screen.getByText('Alpha'));
    const button = screen.getByRole('button');
    expect(button.className).toContain('active');
  });

  it('shows error state when fetch fails', async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 500,
    });
    const onSelect = vi.fn();
    render(<WorkflowList selectedWorkflow={null} onSelect={onSelect} />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch workflows/)).toBeInTheDocument();
    });
  });

  it('shows empty state when no workflows', async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    const onSelect = vi.fn();
    render(<WorkflowList selectedWorkflow={null} onSelect={onSelect} />);

    await waitFor(() => {
      expect(screen.getByText('No workflows found')).toBeInTheDocument();
    });
  });
});
