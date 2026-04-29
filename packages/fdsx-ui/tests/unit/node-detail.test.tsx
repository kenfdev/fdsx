import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { NodeDetail } from '../../src/client/components/NodeDetail.js';
import type { GraphNode, TaskState, ChoiceState, ParallelState, MapState, WaitState } from '../../src/shared/types.js';

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', mockFetch);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function makeNode(label: string, stateType: string, state: Record<string, unknown>): GraphNode {
  return {
    id: 'test-node',
    type: stateType,
    data: {
      label,
      stateType,
      state,
      isStart: false,
    },
    position: { x: 0, y: 0 },
  } as unknown as GraphNode;
}

// Helper to cast NodeDetail to accept workflowPath until it is added to production types
const ND = NodeDetail as React.ComponentType<{
  node: GraphNode;
  onClose: () => void;
  workflowPath: string;
}>;

describe('NodeDetail', () => {
  it('renders node name and type badge', () => {
    const node = makeNode('My Task', 'task', { type: 'task', provider: 'claude' });
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);
    expect(screen.getByText('My Task')).toBeInTheDocument();
    expect(screen.getByText('task')).toBeInTheDocument();
  });

  it('renders close button', () => {
    const node = makeNode('Test', 'task', { type: 'task', provider: 'claude' });
    const onClose = () => {};
    render(<ND node={node} onClose={onClose} workflowPath="test.yaml" />);
    const closeBtn = screen.getByRole('button', { name: '×' });
    expect(closeBtn).toBeInTheDocument();
  });

  it('renders TaskState provider and model', () => {
    const state: TaskState = {
      type: 'task',
      provider: 'claude',
      model: 'claude-3-opus',
      promptTemplate: null,
      promptFile: null,
      command: null,
      resultPath: '$.result',
      resultFile: null,
      extract: null,
      maxIterations: null,
      retry: 3,
      timeoutSeconds: 60,
      providerOptions: null,
      hooks: null,
      next: 'next-state',
      end: null,
    };
    const node = makeNode('Task Node', 'task', state);
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);
    expect(screen.getByText('claude')).toBeInTheDocument();
    expect(screen.getByText('claude-3-opus')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('60s')).toBeInTheDocument();
    expect(screen.getByText('next-state')).toBeInTheDocument();
  });

  it('renders ChoiceState choices', () => {
    const state: ChoiceState = {
      type: 'choice',
      choices: [
        { variable: '$.status', operator: 'equals', value: 'pending', next: 'pending-state' },
        { variable: '$.status', operator: 'equals', value: 'complete', next: 'complete-state' },
      ],
      default: 'default-state',
      maxIterations: null,
      hooks: null,
    };
    const node = makeNode('Choice Node', 'choice', state);
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);
    expect(screen.getByText('Choices')).toBeInTheDocument();
    expect(screen.getByText('pending-state')).toBeInTheDocument();
    expect(screen.getByText('complete-state')).toBeInTheDocument();
    expect(screen.getByText('default-state')).toBeInTheDocument();
  });

  it('renders ParallelState branches', () => {
    const state: ParallelState = {
      type: 'parallel',
      branches: [
        { provider: 'claude', model: null, promptTemplate: 'Branch 1 prompt', promptFile: null, command: null, extract: null, retry: 0, timeoutSeconds: null, providerOptions: null },
        { provider: 'codex', model: null, promptTemplate: null, promptFile: 'branch2.txt', command: null, extract: null, retry: 0, timeoutSeconds: null, providerOptions: null },
      ],
      resultPath: '$.results',
      resultFile: null,
      minSuccess: null,
      maxIterations: null,
      hooks: null,
      next: null,
      end: null,
    };
    const node = makeNode('Parallel Node', 'parallel', state);
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);
    expect(screen.getByText('Branches')).toBeInTheDocument();
    expect(screen.getByText('claude')).toBeInTheDocument();
    expect(screen.getByText('codex')).toBeInTheDocument();
  });

  it('renders MapState iterator info', () => {
    const state: MapState = {
      type: 'map',
      itemsPath: '$.items',
      iterator: {
        states: [
          { type: 'task', name: 'ItemTask', provider: 'claude', model: null, promptTemplate: null, promptFile: null, command: null, resultPath: '', resultFile: null, extract: null, retry: 0, timeoutSeconds: null, providerOptions: null },
        ],
      },
      resultPath: '$.mapped',
      failFast: true,
      maxIterations: null,
      hooks: null,
      next: null,
      end: null,
    };
    const node = makeNode('Map Node', 'map', state);
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);
    expect(screen.getByText('Iterator')).toBeInTheDocument();
    expect(screen.getByText('$.items')).toBeInTheDocument();
    expect(screen.getByText('ItemTask')).toBeInTheDocument();
    expect(screen.getByText('Yes')).toBeInTheDocument();
  });

  it('renders WaitState configuration', () => {
    const state: WaitState = {
      type: 'wait',
      mode: 'confirm',
      message: 'Please confirm',
      choices: ['approve', 'reject'],
      resultPath: '$.result',
      notify: null,
      maxIterations: null,
      hooks: null,
      next: null,
      end: null,
    };
    const node = makeNode('Wait Node', 'wait', state);
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);
    expect(screen.getByText('Wait Configuration')).toBeInTheDocument();
    expect(screen.getByText('confirm')).toBeInTheDocument();
    expect(screen.getByText('Please confirm')).toBeInTheDocument();
    expect(screen.getByText('approve')).toBeInTheDocument();
    expect(screen.getByText('reject')).toBeInTheDocument();
  });
});

describe('NodeDetail PromptContent', () => {
  function makeTaskNode(overrides: Partial<TaskState>): GraphNode {
    const state: TaskState = {
      type: 'task',
      provider: 'claude',
      model: null,
      promptTemplate: null,
      promptFile: null,
      command: null,
      resultPath: '$.result',
      resultFile: null,
      extract: null,
      maxIterations: null,
      retry: 0,
      timeoutSeconds: null,
      providerOptions: null,
      hooks: null,
      next: null,
      end: null,
      ...overrides,
    };
    return makeNode('Task', 'task', state);
  }

  it('fetches file-backed prompt and renders contents', async () => {
    mockFetch.mockResolvedValue({
      json: () => Promise.resolve({ contents: 'Hello from file', file: 'prompts/my-prompt.txt' }),
    });

    const node = makeTaskNode({ promptFile: 'prompts/my-prompt.txt', promptTemplate: null });
    render(<ND node={node} onClose={() => {}} workflowPath="workflows/test.yaml" />);

    await waitFor(() => {
      expect(screen.getByText('Hello from file')).toBeInTheDocument();
    });

    expect(mockFetch).toHaveBeenCalledWith(
      '/api/workflows/workflows/test.yaml/prompt?file=prompts%2Fmy-prompt.txt'
    );
  });

  it('renders "From file:" subheader for file-backed prompt', async () => {
    mockFetch.mockResolvedValue({
      json: () => Promise.resolve({ contents: 'file content', file: 'prompts/x.txt' }),
    });

    const node = makeTaskNode({ promptFile: 'prompts/x.txt', promptTemplate: null });
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);

    await waitFor(() => {
      expect(screen.getByText(/From file:/)).toBeInTheDocument();
    });

    expect(screen.getByText(/prompts\/x\.txt/)).toBeInTheDocument();
  });

  it('renders inline prompt without fetching', () => {
    const node = makeTaskNode({ promptTemplate: 'inline prompt text', promptFile: null });
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);

    expect(screen.getByText('inline prompt text')).toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('shows not-found error with file path when server returns not-found', async () => {
    mockFetch.mockResolvedValue({
      json: () => Promise.resolve({ error: 'not-found', file: 'prompts/missing.txt' }),
    });

    const node = makeTaskNode({ promptFile: 'prompts/missing.txt', promptTemplate: null });
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);

    await waitFor(() => {
      expect(screen.getByText(/Prompt file not found.*prompts\/missing\.txt/)).toBeInTheDocument();
    });
  });

  it('shows outside-workspace error with file path when server returns outside-workspace', async () => {
    mockFetch.mockResolvedValue({
      json: () => Promise.resolve({ error: 'outside-workspace', file: '../secret.txt' }),
    });

    const node = makeTaskNode({ promptFile: '../secret.txt', promptTemplate: null });
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);

    await waitFor(() => {
      expect(
        screen.getByText(/outside the visualized workspace.*\.\.\/secret\.txt/i)
      ).toBeInTheDocument();
    });
  });

  it('shows read-error message with file path when server returns read-error', async () => {
    mockFetch.mockResolvedValue({
      json: () => Promise.resolve({ error: 'read-error', file: 'prompts/unreadable.txt' }),
    });

    const node = makeTaskNode({ promptFile: 'prompts/unreadable.txt', promptTemplate: null });
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);

    await waitFor(() => {
      expect(
        screen.getByText(/Could not read prompt file.*prompts\/unreadable\.txt/i)
      ).toBeInTheDocument();
    });
  });

  it('shows read-error on network failure', async () => {
    mockFetch.mockRejectedValue(new Error('Network error'));

    const node = makeTaskNode({ promptFile: 'prompts/file.txt', promptTemplate: null });
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);

    await waitFor(() => {
      expect(
        screen.getByText(/Could not read prompt file.*prompts\/file\.txt/i)
      ).toBeInTheDocument();
    });
  });

  it('re-fetches when promptFile changes (re-selection triggers new fetch)', async () => {
    mockFetch
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ contents: 'first content', file: 'prompts/first.txt' }),
      })
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ contents: 'second content', file: 'prompts/second.txt' }),
      });

    const firstNode = makeTaskNode({ promptFile: 'prompts/first.txt', promptTemplate: null });
    const { rerender } = render(<ND node={firstNode} onClose={() => {}} workflowPath="test.yaml" />);

    await waitFor(() => {
      expect(screen.getByText('first content')).toBeInTheDocument();
    });

    const secondNode = makeTaskNode({ promptFile: 'prompts/second.txt', promptTemplate: null });
    rerender(<ND node={secondNode} onClose={() => {}} workflowPath="test.yaml" />);

    await waitFor(() => {
      expect(screen.getByText('second content')).toBeInTheDocument();
    });

    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it('shows loading state before fetch resolves', () => {
    // Never resolves so we can observe the loading state
    mockFetch.mockImplementation(() => new Promise(() => {}));

    const node = makeTaskNode({ promptFile: 'prompts/slow.txt', promptTemplate: null });
    render(<ND node={node} onClose={() => {}} workflowPath="test.yaml" />);

    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });
});
