import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { GraphView } from '../../src/client/components/GraphView.js';

const mockFetch = vi.fn();

class MockResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch);
  vi.stubGlobal('ResizeObserver', MockResizeObserver);
});

afterEach(() => {
  vi.restoreAllMocks();
});

const workflowResponse = {
  workflow: { name: 'Test Workflow', description: 'A test workflow', startAt: 'StartState' },
  nodes: [
    { id: 'node-1', type: 'task', data: { label: 'StartState', stateType: 'task', state: { type: 'task', provider: 'claude' }, isStart: true }, position: { x: 100, y: 100 } },
    { id: 'node-2', type: 'task', data: { label: 'EndState', stateType: 'task', state: { type: 'task', provider: 'claude' }, isStart: false }, position: { x: 100, y: 200 } },
  ],
  edges: [
    { id: 'edge-1', source: 'node-1', target: 'node-2', label: null },
  ],
};

describe('GraphView', () => {
  it('shows loading state initially', () => {
    mockFetch.mockImplementation(() => new Promise(() => {}));
    render(<GraphView workflowPath="test/workflow.yaml" />);
    expect(screen.getByText('Loading workflow...')).toBeInTheDocument();
  });

  it('shows error state on fetch failure', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ error: 'Workflow not found' }),
    });
    render(<GraphView workflowPath="test/workflow.yaml" />);
    await waitFor(() => {
      expect(screen.getByText('Workflow not found')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('shows error state on network error', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network error'));
    render(<GraphView workflowPath="test/workflow.yaml" />);
    await waitFor(() => {
      expect(screen.getByText('Network error')).toBeInTheDocument();
    });
  });

  it('shows workflow name in toolbar after loading', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(workflowResponse),
    });
    render(<GraphView workflowPath="test/workflow.yaml" />);
    await waitFor(() => {
      expect(screen.getByText('Test Workflow')).toBeInTheDocument();
    });
  });

  it('has reload button that re-fetches workflow', async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(workflowResponse),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ ...workflowResponse, workflow: { ...workflowResponse.workflow, name: 'Reloaded Workflow' } }),
      });
    
    render(<GraphView workflowPath="test/workflow.yaml" />);
    
    await waitFor(() => {
      expect(screen.getByText('Test Workflow')).toBeInTheDocument();
    });

    const reloadButton = screen.getByRole('button', { name: 'Reload' });
    fireEvent.click(reloadButton);

    await waitFor(() => {
      expect(screen.getByText('Reloaded Workflow')).toBeInTheDocument();
    });

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch).toHaveBeenLastCalledWith('/api/workflows/test/workflow.yaml/reload');
  });
});
