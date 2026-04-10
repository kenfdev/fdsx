import { describe, it, expect, type Mock } from 'vitest';
import type {
  State,
  TaskState,
  ChoiceState,
  ParallelState,
  PassState,
  WaitState,
  MapState,
  Workflow,
  WorkflowFile,
  GraphNode,
  GraphEdge,
  NodeData,
  ChoiceRule,
  Branch,
  ExtractRule,
  IteratorDef,
  IteratorTaskState,
  HookConfig,
  AggregateRule,
} from '../../src/shared/types';

describe('shared/types', () => {
  describe('State discriminated union', () => {
    it('should identify task state by type field', () => {
      const taskState: TaskState = {
        type: 'task',
        provider: 'claude',
        model: 'claude-3-5-sonnet',
        promptTemplate: 'Hello {name}',
        promptFile: null,
        command: null,
        resultPath: '$.result',
        resultFile: null,
        extract: null,
        maxIterations: null,
        retry: 3,
        timeoutSeconds: null,
        providerOptions: null,
        hooks: null,
        next: 'next_state',
        end: null,
      };
      expect(taskState.type).toBe('task');
      expect(taskState.provider).toBe('claude');
    });

    it('should identify choice state by type field', () => {
      const choiceState: ChoiceState = {
        type: 'choice',
        choices: [
          { variable: '$.status', operator: 'equals', value: 'success', next: 'end' },
        ],
        default: null,
        maxIterations: null,
        hooks: null,
      };
      expect(choiceState.type).toBe('choice');
      expect(choiceState.choices[0].operator).toBe('equals');
    });

    it('should identify parallel state by type field', () => {
      const parallelState: ParallelState = {
        type: 'parallel',
        branches: [],
        resultPath: '$.results',
        resultFile: null,
        minSuccess: null,
        maxIterations: null,
        hooks: null,
        next: null,
        end: true,
      };
      expect(parallelState.type).toBe('parallel');
    });

    it('should identify pass state by type field', () => {
      const passState: PassState = {
        type: 'pass',
        parameters: { key: 'value' },
        aggregate: null,
        maxIterations: null,
        hooks: null,
        next: 'next_state',
        end: null,
      };
      expect(passState.type).toBe('pass');
    });

    it('should identify wait state by type field', () => {
      const waitState: WaitState = {
        type: 'wait',
        mode: 'prompt',
        message: 'Continue?',
        choices: ['Yes', 'No'],
        resultPath: '$.choice',
        notify: null,
        maxIterations: null,
        hooks: null,
        next: null,
        end: true,
      };
      expect(waitState.type).toBe('wait');
    });

    it('should identify map state by type field', () => {
      const mapState: MapState = {
        type: 'map',
        itemsPath: '$.items',
        iterator: { states: [] },
        resultPath: '$.results',
        failFast: true,
        maxIterations: null,
        hooks: null,
        next: null,
        end: true,
      };
      expect(mapState.type).toBe('map');
    });

    it('should accept all state types in State union', () => {
      const states: State[] = [
        { type: 'task', provider: 'claude', model: null, promptTemplate: null, promptFile: null, command: null, resultPath: '$.result', resultFile: null, extract: null, maxIterations: null, retry: 3, timeoutSeconds: null, providerOptions: null, hooks: null, next: null, end: null } as TaskState,
        { type: 'choice', choices: [], default: null, maxIterations: null, hooks: null } as ChoiceState,
        { type: 'parallel', branches: [], resultPath: '$.result', resultFile: null, minSuccess: null, maxIterations: null, hooks: null, next: null, end: null } as ParallelState,
        { type: 'pass', parameters: null, aggregate: null, maxIterations: null, hooks: null, next: null, end: null } as PassState,
        { type: 'wait', mode: 'prompt', message: '', choices: [], resultPath: '$.result', notify: null, maxIterations: null, hooks: null, next: null, end: null } as WaitState,
        { type: 'map', itemsPath: '$.items', iterator: { states: [] }, resultPath: '$.results', failFast: true, maxIterations: null, hooks: null, next: null, end: null } as MapState,
      ];
      expect(states).toHaveLength(6);
    });
  });

  describe('Workflow type', () => {
    it('should represent a complete workflow', () => {
      const workflow: Workflow = {
        name: 'test_workflow',
        description: 'A test workflow',
        startAt: 'start',
        states: {
          start: {
            type: 'task',
            provider: 'claude',
            model: 'claude-3-5-sonnet',
            promptTemplate: 'Hello',
            promptFile: null,
            command: null,
            resultPath: '$.result',
            resultFile: null,
            extract: null,
            maxIterations: null,
            retry: 3,
            timeoutSeconds: null,
            providerOptions: null,
            hooks: null,
            next: null,
            end: true,
          },
        },
        version: null,
        maxLoop: 10,
        providers: null,
        hooks: null,
        profiles: null,
      };
      expect(workflow.name).toBe('test_workflow');
      expect(workflow.startAt).toBe('start');
      expect(workflow.states.start.type).toBe('task');
    });

    it('should allow optional providers and profiles', () => {
      const workflow: Workflow = {
        name: 'minimal_workflow',
        description: 'A minimal workflow',
        startAt: 'start',
        states: {},
        version: null,
        maxLoop: 10,
        providers: {
          claude_provider: { api_key: 'test' },
        },
        hooks: null,
        profiles: {
          default: { provider: 'claude', model: 'claude-3-5-sonnet' },
        },
      };
      expect(workflow.providers).toHaveProperty('claude_provider');
      expect(workflow.profiles).toHaveProperty('default');
    });
  });

  describe('WorkflowFile type', () => {
    it('should represent a workflow file with valid flow', () => {
      const workflowFile: WorkflowFile = {
        name: 'test',
        filePath: './workflows/test.yaml',
        relativePath: 'workflows/test.yaml',
      };
      expect(workflowFile.name).toBe('test');
      expect(workflowFile.filePath).toBe('./workflows/test.yaml');
      expect(workflowFile.relativePath).toBe('workflows/test.yaml');
    });

    it('should represent a workflow file with parse error', () => {
      const workflowFile: WorkflowFile = {
        name: 'broken',
        filePath: './workflows/broken.yaml',
        relativePath: 'workflows/broken.yaml',
      };
      expect(workflowFile.name).toBe('broken');
    });
  });

  describe('ChoiceRule type', () => {
    it('should support all operators', () => {
      const rules: ChoiceRule[] = [
        { variable: '$.status', operator: 'equals', value: 'ok', next: 'state1' },
        { variable: '$.count', operator: 'not_equals', value: 0, next: 'state2' },
        { variable: '$.score', operator: 'greater_than', value: 10, next: 'state3' },
        { variable: '$.score', operator: 'less_than', value: 100, next: 'state4' },
        { variable: '$.tags', operator: 'contains', value: 'urgent', next: 'state5' },
      ];
      expect(rules).toHaveLength(5);
    });
  });

  describe('ExtractRule type', () => {
    it('should support multiple strategies', () => {
      const extract: ExtractRule = {
        strategy: ['json', 'regex', 'keyword'],
        pattern: 'result',
        resultPath: '$.output',
      };
      expect(extract.strategy).toContain('json');
      expect(extract.strategy).toContain('regex');
    });

    it('should allow LLM classify fallback', () => {
      const extract: ExtractRule = {
        strategy: ['json'],
        pattern: 'result',
        resultPath: '$.output',
      };
      expect(extract.strategy).toContain('json');
    });
  });

  describe('Branch type', () => {
    it('should support system provider with command', () => {
      const branch: Branch = {
        provider: 'system',
        model: null,
        promptTemplate: null,
        promptFile: null,
        command: 'echo hello',
        extract: null,
        retry: 3,
        timeoutSeconds: null,
        providerOptions: null,
      };
      expect(branch.provider).toBe('system');
      expect(branch.command).toBe('echo hello');
    });

    it('should support LLM provider with model', () => {
      const branch: Branch = {
        provider: 'claude',
        model: 'claude-3-5-sonnet',
        promptTemplate: 'Hello {name}',
        promptFile: null,
        command: null,
        extract: null,
        retry: 3,
        timeoutSeconds: 60,
        providerOptions: { temperature: 0.7 },
      };
      expect(branch.provider).toBe('claude');
      expect(branch.model).toBe('claude-3-5-sonnet');
      expect(branch.providerOptions?.temperature).toBe(0.7);
    });
  });

  describe('IteratorDef and IteratorTaskState types', () => {
    it('should support iterator with multiple task states', () => {
      const iterator: IteratorDef = {
        states: [
          {
            type: 'task',
            name: 'process_item',
            provider: 'claude',
            model: 'claude-3-5-sonnet',
            promptTemplate: 'Process {item}',
            promptFile: null,
            command: null,
            resultPath: '$.result',
            resultFile: null,
            extract: null,
            retry: 3,
            timeoutSeconds: null,
            providerOptions: null,
          },
        ],
      };
      expect(iterator.states).toHaveLength(1);
      expect(iterator.states[0].name).toBe('process_item');
    });

    it('should differentiate IteratorTaskState from TaskState', () => {
      const iteratorState: IteratorTaskState = {
        type: 'task',
        name: 'iterate_task',
        provider: 'claude',
        model: 'claude-3-5-sonnet',
        promptTemplate: 'Iterate {item}',
        promptFile: null,
        command: null,
        resultPath: '$.result',
        resultFile: null,
        extract: null,
        retry: 3,
        timeoutSeconds: null,
        providerOptions: null,
      };
      expect(iteratorState.name).toBe('iterate_task');
      expect(iteratorState).not.toHaveProperty('next');
      expect(iteratorState).not.toHaveProperty('end');
    });
  });

  describe('HookConfig type', () => {
    it('should support start and complete hooks', () => {
      const hooks: HookConfig = {
        onStart: [
          { command: 'echo starting', onFailure: 'warn' },
        ],
        onComplete: [
          { command: 'echo done', onFailure: 'abort' },
        ],
      };
      expect(hooks.onStart).toHaveLength(1);
      expect(hooks.onComplete[0].onFailure).toBe('abort');
    });
  });

  describe('AggregateRule type', () => {
    it('should support aggregate configuration', () => {
      const aggregate: AggregateRule = {
        source: '$.parallel_results',
        field: 'status',
        strategy: 'majority',
        match: 'success',
        noMatch: 'failure',
        resultPath: '$.aggregated',
      };
      expect(aggregate.strategy).toBe('majority');
      expect(aggregate.match).toBe('success');
    });
  });

  describe('NodeData type', () => {
    it('should hold all necessary node information', () => {
      const nodeData: NodeData = {
        label: 'my_task',
        stateType: 'task',
        state: {
          type: 'task',
          provider: 'claude',
          model: 'claude-3-5-sonnet',
          promptTemplate: 'Hello',
          promptFile: null,
          command: null,
          resultPath: '$.result',
          resultFile: null,
          extract: null,
          maxIterations: null,
          retry: 3,
          timeoutSeconds: null,
          providerOptions: null,
          hooks: null,
          next: null,
          end: null,
        },
        isStart: true,
      };
      expect(nodeData.label).toBe('my_task');
      expect(nodeData.stateType).toBe('task');
      expect(nodeData.isStart).toBe(true);
    });
  });

  describe('GraphNode and GraphEdge types', () => {
    it('should be compatible with React Flow types', () => {
      const node: GraphNode = {
        id: 'node-1',
        type: 'workflowNode',
        position: { x: 0, y: 0 },
        data: {
          label: 'test',
          stateType: 'task',
          state: {
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
            retry: 3,
            timeoutSeconds: null,
            providerOptions: null,
            hooks: null,
            next: null,
            end: null,
          },
          isStart: false,
        },
      };
      expect(node.id).toBe('node-1');
      expect(node.data.label).toBe('test');
    });

    it('should support edge with optional label', () => {
      const edge: GraphEdge = {
        id: 'edge-1',
        source: 'node-1',
        target: 'node-2',
        label: 'on_success',
      };
      expect(edge.source).toBe('node-1');
      expect(edge.label).toBe('on_success');
    });

    it('should support edge without label', () => {
      const edge: GraphEdge = {
        id: 'edge-2',
        source: 'node-2',
        target: 'node-3',
      };
      expect(edge.label).toBeUndefined();
    });
  });
});
