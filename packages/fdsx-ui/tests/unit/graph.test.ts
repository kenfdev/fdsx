import { describe, it, expect } from 'vitest';
import { parseWorkflow } from '../../src/server/parser.js';
import { transformWorkflow } from '../../src/server/graph.js';

describe('transformWorkflow', () => {
  it('TaskState with next produces one edge', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo first
    result_path: $.result
    next: second
  second:
    type: task
    provider: system
    command: echo second
    result_path: $.result
    end: true
`);
    const { nodes, edges } = transformWorkflow(workflow);

    expect(nodes).toHaveLength(2);
    expect(edges).toHaveLength(1);
    expect(edges[0].source).toBe('first');
    expect(edges[0].target).toBe('second');
  });

  it('TaskState with end: true produces no edges', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: start
states:
  start:
    type: task
    provider: system
    command: echo
    result_path: $.result
    end: true
`);
    const { edges } = transformWorkflow(workflow);

    expect(edges).toHaveLength(0);
  });

  it('ChoiceState produces edges per choice and default', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: check
states:
  check:
    type: choice
    choices:
      - variable: $.status
        operator: equals
        value: success
        next: success
      - variable: $.status
        operator: equals
        value: failure
        next: failure
    default: unknown
  success:
    type: task
    provider: system
    command: echo success
    result_path: $.result
    end: true
  failure:
    type: task
    provider: system
    command: echo failure
    result_path: $.result
    end: true
  unknown:
    type: task
    provider: system
    command: echo unknown
    result_path: $.result
    end: true
`);
    const { edges } = transformWorkflow(workflow);

    expect(edges).toHaveLength(3);
    const choiceEdges = edges.filter((e) => e.edgeType === 'choice');
    expect(choiceEdges).toHaveLength(3);
    const defaultEdge = edges.find((e) => e.label === 'default');
    expect(defaultEdge).toBeDefined();
    expect(defaultEdge?.target).toBe('unknown');
  });

  it('ParallelState produces one edge to next', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: start
states:
  start:
    type: task
    provider: system
    command: echo start
    result_path: $.result
    next: parallel
  parallel:
    type: parallel
    branches:
      - provider: system
        command: echo branch1
        result_path: $.branch1
    result_path: $.result
    next: end
  end:
    type: task
    provider: system
    command: echo end
    result_path: $.result
    end: true
`);
    const { edges } = transformWorkflow(workflow);

    expect(edges).toHaveLength(2);
    const parallelEdge = edges.find((e) => e.edgeType === 'parallel');
    expect(parallelEdge).toBeDefined();
    expect(parallelEdge?.source).toBe('parallel');
    expect(parallelEdge?.target).toBe('end');
  });

  it('PassState produces edge to next', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: start
states:
  start:
    type: task
    provider: system
    command: echo start
    result_path: $.result
    next: pass
  pass:
    type: pass
    next: end
  end:
    type: task
    provider: system
    command: echo end
    result_path: $.result
    end: true
`);
    const { edges } = transformWorkflow(workflow);

    expect(edges).toHaveLength(2);
    const passEdge = edges.find((e) => e.source === 'pass');
    expect(passEdge).toBeDefined();
    expect(passEdge?.target).toBe('end');
  });

  it('WaitState produces edge to next', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: start
states:
  start:
    type: task
    provider: system
    command: echo start
    result_path: $.result
    next: wait
  wait:
    type: wait
    mode: prompt
    result_path: $.result
    next: end
  end:
    type: task
    provider: system
    command: echo end
    result_path: $.result
    end: true
`);
    const { edges } = transformWorkflow(workflow);

    expect(edges).toHaveLength(2);
    const waitEdge = edges.find((e) => e.source === 'wait');
    expect(waitEdge).toBeDefined();
    expect(waitEdge?.target).toBe('end');
  });

  it('MapState produces edge to next', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: start
states:
  start:
    type: task
    provider: system
    command: echo start
    result_path: $.result
    next: map
  map:
    type: map
    items_path: $.items
    iterator:
      states:
        - name: item
          provider: system
          command: echo item
          result_path: $.item
    result_path: $.result
    next: end
  end:
    type: task
    provider: system
    command: echo end
    result_path: $.result
    end: true
`);
    const { edges } = transformWorkflow(workflow);

    expect(edges).toHaveLength(2);
    const mapEdge = edges.find((e) => e.source === 'map');
    expect(mapEdge).toBeDefined();
    expect(mapEdge?.target).toBe('end');
  });

  it('Start node gets isStart: true', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo first
    result_path: $.result
    end: true
`);
    const { nodes } = transformWorkflow(workflow);

    const startNode = nodes.find((n) => n.id === 'first');
    expect(startNode?.data.isStart).toBe(true);
  });

  it('Non-start nodes get isStart: false', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo first
    result_path: $.result
    next: second
  second:
    type: task
    provider: system
    command: echo second
    result_path: $.result
    end: true
`);
    const { nodes } = transformWorkflow(workflow);

    const secondNode = nodes.find((n) => n.id === 'second');
    expect(secondNode?.data.isStart).toBe(false);
  });

  it('dagre layout produces valid positions for all nodes', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo first
    result_path: $.result
    next: second
  second:
    type: task
    provider: system
    command: echo second
    result_path: $.result
    end: true
`);
    const { nodes } = transformWorkflow(workflow);

    for (const node of nodes) {
      expect(Number.isFinite(node.position.x)).toBe(true);
      expect(Number.isFinite(node.position.y)).toBe(true);
    }
  });

  it('Multi-state workflow produces correct node count', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo first
    result_path: $.result
    next: second
  second:
    type: task
    provider: system
    command: echo second
    result_path: $.result
    next: third
  third:
    type: task
    provider: system
    command: echo third
    result_path: $.result
    end: true
`);
    const { nodes } = transformWorkflow(workflow);

    expect(nodes).toHaveLength(3);
  });

  it('nodes have correct type set to state type', () => {
    const workflow = parseWorkflow(`
name: Test
start_at: check
states:
  check:
    type: choice
    choices:
      - variable: $.status
        operator: equals
        value: success
        next: success
    default: success
  success:
    type: task
    provider: system
    command: echo success
    result_path: $.result
    end: true
`);
    const { nodes } = transformWorkflow(workflow);

    const checkNode = nodes.find((n) => n.id === 'check');
    expect(checkNode?.type).toBe('choice');
    const successNode = nodes.find((n) => n.id === 'success');
    expect(successNode?.type).toBe('task');
  });
});
