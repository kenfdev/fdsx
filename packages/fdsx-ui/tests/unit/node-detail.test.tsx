import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { NodeDetail } from '../../src/client/components/NodeDetail.js';
import type { GraphNode, TaskState, ChoiceState, ParallelState, MapState, WaitState } from '../../src/shared/types.js';

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

describe('NodeDetail', () => {
  it('renders node name and type badge', () => {
    const node = makeNode('My Task', 'task', { type: 'task', provider: 'claude' });
    render(<NodeDetail node={node} onClose={() => {}} />);
    expect(screen.getByText('My Task')).toBeInTheDocument();
    expect(screen.getByText('task')).toBeInTheDocument();
  });

  it('renders close button', () => {
    const node = makeNode('Test', 'task', { type: 'task', provider: 'claude' });
    const onClose = () => {};
    render(<NodeDetail node={node} onClose={onClose} />);
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
    render(<NodeDetail node={node} onClose={() => {}} />);
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
    render(<NodeDetail node={node} onClose={() => {}} />);
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
    render(<NodeDetail node={node} onClose={() => {}} />);
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
    render(<NodeDetail node={node} onClose={() => {}} />);
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
    render(<NodeDetail node={node} onClose={() => {}} />);
    expect(screen.getByText('Wait Configuration')).toBeInTheDocument();
    expect(screen.getByText('confirm')).toBeInTheDocument();
    expect(screen.getByText('Please confirm')).toBeInTheDocument();
    expect(screen.getByText('approve')).toBeInTheDocument();
    expect(screen.getByText('reject')).toBeInTheDocument();
  });
});
