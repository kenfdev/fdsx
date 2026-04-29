import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TaskNode } from '../../src/client/components/nodes/TaskNode.js';
import { ChoiceNode } from '../../src/client/components/nodes/ChoiceNode.js';
import { ParallelNode } from '../../src/client/components/nodes/ParallelNode.js';
import { PassNode } from '../../src/client/components/nodes/PassNode.js';
import { WaitNode } from '../../src/client/components/nodes/WaitNode.js';
import { MapNode } from '../../src/client/components/nodes/MapNode.js';
import { nodeTypes } from '../../src/client/components/nodes/index.js';

vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Top: 'top', Bottom: 'bottom' },
}));

function makeNodeProps(data: Record<string, unknown>) {
  return { id: 'test', data, type: (data as { stateType: string }).stateType } as any;
}

describe('nodeTypes registry', () => {
  it('has exactly 6 node types', () => {
    expect(Object.keys(nodeTypes)).toHaveLength(6);
  });

  it('exports all expected keys', () => {
    expect(nodeTypes).toHaveProperty('task');
    expect(nodeTypes).toHaveProperty('choice');
    expect(nodeTypes).toHaveProperty('parallel');
    expect(nodeTypes).toHaveProperty('pass');
    expect(nodeTypes).toHaveProperty('wait');
    expect(nodeTypes).toHaveProperty('map');
  });
});

describe('TaskNode', () => {
  it('renders state name and provider sublabel', () => {
    const props = makeNodeProps({
      label: 'Generate Code',
      stateType: 'task',
      isStart: false,
      state: { type: 'task', provider: 'claude' },
    });
    render(<TaskNode {...props} />);
    expect(screen.getByText('Generate Code')).toBeInTheDocument();
    expect(screen.getByText('claude')).toBeInTheDocument();
  });

  it('applies startNode class when isStart is true', () => {
    const props = makeNodeProps({
      label: 'Start Task',
      stateType: 'task',
      isStart: true,
      state: { type: 'task', provider: 'claude' },
    });
    const { container } = render(<TaskNode {...props} />);
    expect(container.querySelector('.startNode')).toBeTruthy();
  });

  it('does not apply startNode class when isStart is false', () => {
    const props = makeNodeProps({
      label: 'Regular Task',
      stateType: 'task',
      isStart: false,
      state: { type: 'task', provider: 'codex' },
    });
    const { container } = render(<TaskNode {...props} />);
    expect(container.querySelector('.startNode')).toBeFalsy();
  });

  it('applies endNode class when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Task',
      stateType: 'task',
      isStart: false,
      isEnd: true,
      state: { type: 'task', provider: 'claude' },
    });
    const { container } = render(<TaskNode {...props} />);
    expect(container.querySelector('.endNode')).toBeTruthy();
    expect(container.querySelector('.startNode')).toBeFalsy();
  });

  it('does not apply endNode class when isEnd is false', () => {
    const props = makeNodeProps({
      label: 'Middle Task',
      stateType: 'task',
      isStart: false,
      isEnd: false,
      state: { type: 'task', provider: 'claude' },
    });
    const { container } = render(<TaskNode {...props} />);
    expect(container.querySelector('.endNode')).toBeFalsy();
  });

  it('startNode wins when both isStart and isEnd are true', () => {
    const props = makeNodeProps({
      label: 'Only Task',
      stateType: 'task',
      isStart: true,
      isEnd: true,
      state: { type: 'task', provider: 'claude' },
    });
    const { container } = render(<TaskNode {...props} />);
    expect(container.querySelector('.startNode')).toBeTruthy();
    expect(container.querySelector('.endNode')).toBeFalsy();
  });

  it('renders play icon when isStart is true', () => {
    const props = makeNodeProps({
      label: 'Start Task',
      stateType: 'task',
      isStart: true,
      isEnd: false,
      state: { type: 'task', provider: 'claude' },
    });
    render(<TaskNode {...props} />);
    expect(screen.getByText('▶')).toBeInTheDocument();
  });

  it('renders stop icon when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Task',
      stateType: 'task',
      isStart: false,
      isEnd: true,
      state: { type: 'task', provider: 'claude' },
    });
    render(<TaskNode {...props} />);
    expect(screen.getByText('■')).toBeInTheDocument();
  });

  it('renders no icon for plain node', () => {
    const props = makeNodeProps({
      label: 'Middle Task',
      stateType: 'task',
      isStart: false,
      isEnd: false,
      state: { type: 'task', provider: 'claude' },
    });
    render(<TaskNode {...props} />);
    expect(screen.queryByText('▶')).not.toBeInTheDocument();
    expect(screen.queryByText('■')).not.toBeInTheDocument();
  });
});

describe('ChoiceNode', () => {
  it('renders label and has diamondOuter and diamondInner nested', () => {
    const props = makeNodeProps({
      label: 'Check Status',
      stateType: 'choice',
      isStart: false,
      isEnd: false,
      state: { type: 'choice' },
    });
    const { container } = render(<ChoiceNode {...props} />);
    expect(screen.getByText('Check Status')).toBeInTheDocument();
    expect(container.querySelector('.diamondOuter')).toBeTruthy();
    expect(container.querySelector('.diamondInner')).toBeTruthy();
    expect(container.querySelector('.diamondOuter .diamondInner')).toBeTruthy();
  });

  it('applies startNode class on diamondOuter when isStart is true', () => {
    const props = makeNodeProps({
      label: 'Start Decision',
      stateType: 'choice',
      isStart: true,
      isEnd: false,
      state: { type: 'choice' },
    });
    const { container } = render(<ChoiceNode {...props} />);
    expect(container.querySelector('.diamondOuter.startNode')).toBeTruthy();
  });

  it('applies endNode class on diamondOuter when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Decision',
      stateType: 'choice',
      isStart: false,
      isEnd: true,
      state: { type: 'choice' },
    });
    const { container } = render(<ChoiceNode {...props} />);
    expect(container.querySelector('.diamondOuter.endNode')).toBeTruthy();
    expect(container.querySelector('.diamondOuter.startNode')).toBeFalsy();
  });

  it('startNode wins when both isStart and isEnd are true', () => {
    const props = makeNodeProps({
      label: 'Only Decision',
      stateType: 'choice',
      isStart: true,
      isEnd: true,
      state: { type: 'choice' },
    });
    const { container } = render(<ChoiceNode {...props} />);
    expect(container.querySelector('.diamondOuter.startNode')).toBeTruthy();
    expect(container.querySelector('.diamondOuter.endNode')).toBeFalsy();
  });

  it('does not apply role class when neither flag is set', () => {
    const props = makeNodeProps({
      label: 'Plain Decision',
      stateType: 'choice',
      isStart: false,
      isEnd: false,
      state: { type: 'choice' },
    });
    const { container } = render(<ChoiceNode {...props} />);
    expect(container.querySelector('.startNode')).toBeFalsy();
    expect(container.querySelector('.endNode')).toBeFalsy();
  });

  it('renders play icon when isStart is true', () => {
    const props = makeNodeProps({
      label: 'Start Decision',
      stateType: 'choice',
      isStart: true,
      isEnd: false,
      state: { type: 'choice' },
    });
    render(<ChoiceNode {...props} />);
    expect(screen.getByText('▶')).toBeInTheDocument();
  });

  it('renders stop icon when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Decision',
      stateType: 'choice',
      isStart: false,
      isEnd: true,
      state: { type: 'choice' },
    });
    render(<ChoiceNode {...props} />);
    expect(screen.getByText('■')).toBeInTheDocument();
  });

  it('renders no icon for plain decision', () => {
    const props = makeNodeProps({
      label: 'Plain Decision',
      stateType: 'choice',
      isStart: false,
      isEnd: false,
      state: { type: 'choice' },
    });
    render(<ChoiceNode {...props} />);
    expect(screen.queryByText('▶')).not.toBeInTheDocument();
    expect(screen.queryByText('■')).not.toBeInTheDocument();
  });
});

describe('ParallelNode', () => {
  it('renders label and branch count', () => {
    const props = makeNodeProps({
      label: 'Fan Out',
      stateType: 'parallel',
      isStart: false,
      state: { type: 'parallel', branches: [{}, {}] },
    });
    render(<ParallelNode {...props} />);
    expect(screen.getByText('Fan Out')).toBeInTheDocument();
    expect(screen.getByText('2 branches')).toBeInTheDocument();
  });

  it('shows singular branch for single branch', () => {
    const props = makeNodeProps({
      label: 'Single',
      stateType: 'parallel',
      isStart: false,
      state: { type: 'parallel', branches: [{}] },
    });
    render(<ParallelNode {...props} />);
    expect(screen.getByText('1 branch')).toBeInTheDocument();
  });

  it('applies endNode class when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Parallel',
      stateType: 'parallel',
      isStart: false,
      isEnd: true,
      state: { type: 'parallel', branches: [{}] },
    });
    const { container } = render(<ParallelNode {...props} />);
    expect(container.querySelector('.endNode')).toBeTruthy();
    expect(container.querySelector('.startNode')).toBeFalsy();
  });

  it('renders stop icon when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Parallel',
      stateType: 'parallel',
      isStart: false,
      isEnd: true,
      state: { type: 'parallel', branches: [{}] },
    });
    render(<ParallelNode {...props} />);
    expect(screen.getByText('■')).toBeInTheDocument();
  });
});

describe('PassNode', () => {
  it('renders label and has passNode class', () => {
    const props = makeNodeProps({
      label: 'Skip Step',
      stateType: 'pass',
      isStart: false,
      state: { type: 'pass' },
    });
    const { container } = render(<PassNode {...props} />);
    expect(screen.getByText('Skip Step')).toBeInTheDocument();
    expect(container.querySelector('.passNode')).toBeTruthy();
  });

  it('applies endNode class when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Pass',
      stateType: 'pass',
      isStart: false,
      isEnd: true,
      state: { type: 'pass' },
    });
    const { container } = render(<PassNode {...props} />);
    expect(container.querySelector('.endNode')).toBeTruthy();
    expect(container.querySelector('.startNode')).toBeFalsy();
  });

  it('renders stop icon when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Pass',
      stateType: 'pass',
      isStart: false,
      isEnd: true,
      state: { type: 'pass' },
    });
    render(<PassNode {...props} />);
    expect(screen.getByText('■')).toBeInTheDocument();
  });
});

describe('WaitNode', () => {
  it('renders label and mode sublabel', () => {
    const props = makeNodeProps({
      label: 'Wait for Input',
      stateType: 'wait',
      isStart: false,
      state: { type: 'wait', mode: 'confirm' },
    });
    render(<WaitNode {...props} />);
    expect(screen.getByText('Wait for Input')).toBeInTheDocument();
    expect(screen.getByText('confirm')).toBeInTheDocument();
  });

  it('applies endNode class when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Wait',
      stateType: 'wait',
      isStart: false,
      isEnd: true,
      state: { type: 'wait', mode: 'confirm' },
    });
    const { container } = render(<WaitNode {...props} />);
    expect(container.querySelector('.endNode')).toBeTruthy();
    expect(container.querySelector('.startNode')).toBeFalsy();
  });

  it('renders stop icon when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Wait',
      stateType: 'wait',
      isStart: false,
      isEnd: true,
      state: { type: 'wait', mode: 'confirm' },
    });
    render(<WaitNode {...props} />);
    expect(screen.getByText('■')).toBeInTheDocument();
  });
});

describe('MapNode', () => {
  it('renders label and iterator sublabel', () => {
    const props = makeNodeProps({
      label: 'Process Items',
      stateType: 'map',
      isStart: false,
      state: { type: 'map' },
    });
    render(<MapNode {...props} />);
    expect(screen.getByText('Process Items')).toBeInTheDocument();
    expect(screen.getByText('iterator')).toBeInTheDocument();
  });

  it('has mapNode class', () => {
    const props = makeNodeProps({
      label: 'Map Step',
      stateType: 'map',
      isStart: false,
      state: { type: 'map' },
    });
    const { container } = render(<MapNode {...props} />);
    expect(container.querySelector('.mapNode')).toBeTruthy();
  });

  it('applies endNode class when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Map',
      stateType: 'map',
      isStart: false,
      isEnd: true,
      state: { type: 'map' },
    });
    const { container } = render(<MapNode {...props} />);
    expect(container.querySelector('.endNode')).toBeTruthy();
    expect(container.querySelector('.startNode')).toBeFalsy();
  });

  it('renders stop icon when isEnd is true', () => {
    const props = makeNodeProps({
      label: 'End Map',
      stateType: 'map',
      isStart: false,
      isEnd: true,
      state: { type: 'map' },
    });
    render(<MapNode {...props} />);
    expect(screen.getByText('■')).toBeInTheDocument();
  });
});
