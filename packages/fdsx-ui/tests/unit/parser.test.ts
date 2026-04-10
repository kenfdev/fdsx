import { describe, it, expect } from 'vitest';
import { parseWorkflow, WorkflowParseError } from '../../src/server/parser.js';

describe('parseWorkflow', () => {
  it('parses a complete task state with all fields', () => {
    const yaml = `
name: Test Workflow
description: A test workflow
start_at: my_task
version: '1.0'
max_loop: 5

states:
  my_task:
    type: task
    provider: claude
    model: claude-sonnet-4
    prompt_template: "Hello {name}"
    prompt_file: null
    command: null
    result_path: $.result
    result_file: null
    extract:
      strategy:
        - regex
      pattern: "result: (\\\\w+)"
      result_path: $.extracted
    max_iterations: 10
    retry: 5
    timeout_seconds: 60
    provider_options:
      temperature: 0.7
    hooks:
      on_start:
        - command: echo starting
          on_failure: warn
      on_complete:
        - command: echo done
          on_failure: abort
      on_failure: []
    next: next_task
    end: null
`;
    const workflow = parseWorkflow(yaml);

    expect(workflow.name).toBe('Test Workflow');
    expect(workflow.description).toBe('A test workflow');
    expect(workflow.startAt).toBe('my_task');
    expect(workflow.version).toBe('1.0');
    expect(workflow.maxLoop).toBe(5);

    const task = workflow.states['my_task'] as { type: 'task'; provider: string; model: string | null; promptTemplate: string | null; resultPath: string; extract: { strategy: string[]; pattern: string; resultPath: string }; maxIterations: number | null; retry: number; timeoutSeconds: number | null; providerOptions: Record<string, unknown> | null; hooks: { onStart: { command: string; onFailure: string }[]; onComplete: { command: string; onFailure: string }[]; onFailure: { command: string; onFailure: string }[] } | null; next: string | null; end: boolean | null };
    expect(task.type).toBe('task');
    expect(task.provider).toBe('claude');
    expect(task.model).toBe('claude-sonnet-4');
    expect(task.promptTemplate).toBe('Hello {name}');
    expect(task.resultPath).toBe('$.result');
    expect(task.extract?.strategy).toEqual(['regex']);
    expect(task.extract?.pattern).toBe('result: (\\w+)');
    expect(task.extract?.resultPath).toBe('$.extracted');
    expect(task.maxIterations).toBe(10);
    expect(task.retry).toBe(5);
    expect(task.timeoutSeconds).toBe(60);
    expect(task.providerOptions).toEqual({ temperature: 0.7 });
    expect(task.hooks?.onStart).toHaveLength(1);
    expect(task.hooks?.onStart[0].command).toBe('echo starting');
    expect(task.hooks?.onStart[0].onFailure).toBe('warn');
    expect(task.hooks?.onComplete[0].onFailure).toBe('abort');
    expect(task.next).toBe('next_task');
  });

  it('parses choice state with rules and default', () => {
    const yaml = `
name: Choice Flow
start_at: decision
states:
  decision:
    type: choice
    choices:
      - variable: $.status
        operator: equals
        value: success
        next: success_path
      - variable: $.status
        operator: contains
        value: error
        next: failure_path
    default: unknown_path
    max_iterations: null
    hooks: null
`;
    const workflow = parseWorkflow(yaml);

    const choice = workflow.states['decision'] as { type: 'choice'; choices: { variable: string; operator: string; value: unknown; next: string }[]; default: string | null };
    expect(choice.type).toBe('choice');
    expect(choice.choices).toHaveLength(2);
    expect(choice.choices[0].variable).toBe('$.status');
    expect(choice.choices[0].operator).toBe('equals');
    expect(choice.choices[0].value).toBe('success');
    expect(choice.choices[0].next).toBe('success_path');
    expect(choice.default).toBe('unknown_path');
  });

  it('parses parallel state with branches', () => {
    const yaml = `
name: Parallel Flow
start_at: parallel_step
states:
  parallel_step:
    type: parallel
    branches:
      - provider: claude
        model: claude-sonnet-4
        prompt_template: "Task 1"
        prompt_file: null
        command: null
        extract:
          strategy:
            - keyword
          pattern: done
          result_path: $.r1
        retry: 3
        timeout_seconds: 30
        provider_options: null
      - provider: system
        command: echo done
        extract: null
        retry: 0
        timeout_seconds: null
        provider_options: null
    result_path: $.parallel_results
    result_file: null
    min_success: 2
    next: aggregate
    end: null
`;
    const workflow = parseWorkflow(yaml);

    const parallel = workflow.states['parallel_step'] as { type: 'parallel'; branches: { provider: string; model: string | null; promptTemplate: string | null; command: string | null; extract: { strategy: string[]; pattern: string; resultPath: string } | null; retry: number; timeoutSeconds: number | null }[]; resultPath: string; minSuccess: number | null; next: string | null };
    expect(parallel.type).toBe('parallel');
    expect(parallel.branches).toHaveLength(2);
    expect(parallel.branches[0].provider).toBe('claude');
    expect(parallel.branches[0].model).toBe('claude-sonnet-4');
    expect(parallel.branches[0].promptTemplate).toBe('Task 1');
    expect(parallel.branches[0].retry).toBe(3);
    expect(parallel.branches[1].provider).toBe('system');
    expect(parallel.branches[1].retry).toBe(0);
    expect(parallel.resultPath).toBe('$.parallel_results');
    expect(parallel.minSuccess).toBe(2);
    expect(parallel.next).toBe('aggregate');
  });

  it('parses pass state with parameters and aggregate', () => {
    const yaml = `
name: Pass Flow
start_at: pass_step
states:
  pass_step:
    type: pass
    parameters:
      $.items:
        - a
        - b
    aggregate:
      source: $.items
      field: value
      strategy: majority
      match: yes
      no_match: no
      result_path: $.result
    next: next_step
    end: null
`;
    const workflow = parseWorkflow(yaml);

    const pass = workflow.states['pass_step'] as { type: 'pass'; parameters: Record<string, unknown> | null; aggregate: { source: string; field: string; strategy: string; match: string; noMatch: string; resultPath: string } | null; next: string | null };
    expect(pass.type).toBe('pass');
    expect(pass.parameters).toEqual({ '$.items': ['a', 'b'] });
    expect(pass.aggregate?.source).toBe('$.items');
    expect(pass.aggregate?.field).toBe('value');
    expect(pass.aggregate?.strategy).toBe('majority');
    expect(pass.aggregate?.match).toBe('yes');
    expect(pass.aggregate?.noMatch).toBe('no');
    expect(pass.aggregate?.resultPath).toBe('$.result');
    expect(pass.next).toBe('next_step');
  });

  it('parses wait state with message and choices', () => {
    const yaml = `
name: Wait Flow
start_at: wait_step
states:
  wait_step:
    type: wait
    mode: prompt
    message: "Please approve"
    choices:
      - approve
      - reject
      - retry
    result_path: $.approval
    notify: null
    next: route
    end: null
`;
    const workflow = parseWorkflow(yaml);

    const wait = workflow.states['wait_step'] as { type: 'wait'; mode: string; message: string; choices: string[]; resultPath: string; next: string | null };
    expect(wait.type).toBe('wait');
    expect(wait.mode).toBe('prompt');
    expect(wait.message).toBe('Please approve');
    expect(wait.choices).toEqual(['approve', 'reject', 'retry']);
    expect(wait.resultPath).toBe('$.approval');
    expect(wait.next).toBe('route');
  });

  it('parses map state with iterator', () => {
    const yaml = `
name: Map Flow
start_at: map_step
states:
  map_step:
    type: map
    items_path: $.items
    iterator:
      states:
        - name: step1
          type: task
          provider: system
          command: echo "{item}"
          result_path: $.iter.step1
          retry: 0
          timeout_seconds: null
          provider_options: null
        - name: step2
          type: task
          provider: system
          command: echo done
          result_path: $.iter.step2
          retry: 3
          timeout_seconds: null
          provider_options: null
    result_path: $.map_results
    fail_fast: false
    next: after_map
    end: null
`;
    const workflow = parseWorkflow(yaml);

    const map = workflow.states['map_step'] as { type: 'map'; itemsPath: string; iterator: { states: { name: string; provider: string; command: string; resultPath: string; retry: number }[] }; resultPath: string; failFast: boolean; next: string | null };
    expect(map.type).toBe('map');
    expect(map.itemsPath).toBe('$.items');
    expect(map.iterator.states).toHaveLength(2);
    expect(map.iterator.states[0].name).toBe('step1');
    expect(map.iterator.states[0].command).toBe('echo "{item}"');
    expect(map.iterator.states[0].retry).toBe(0);
    expect(map.iterator.states[1].retry).toBe(3);
    expect(map.resultPath).toBe('$.map_results');
    expect(map.failFast).toBe(false);
    expect(map.next).toBe('after_map');
  });

  it('maps snake_case keys to camelCase', () => {
    const yaml = `
name: Snake Test
start_at: task_step
states:
  task_step:
    type: task
    provider: system
    prompt_template: hello
    prompt_file: template.txt
    result_path: $.output
    result_file: output.json
    timeout_seconds: 45
    provider_options:
      key: value
    max_iterations: 5
    retry: 2
`;
    const workflow = parseWorkflow(yaml);

    const task = workflow.states['task_step'] as { promptTemplate: string | null; promptFile: string | null; resultPath: string; resultFile: string | null; timeoutSeconds: number | null; providerOptions: Record<string, unknown> | null; maxIterations: number | null; retry: number };
    expect(task.promptTemplate).toBe('hello');
    expect(task.promptFile).toBe('template.txt');
    expect(task.resultPath).toBe('$.output');
    expect(task.resultFile).toBe('output.json');
    expect(task.timeoutSeconds).toBe(45);
    expect(task.providerOptions).toEqual({ key: 'value' });
    expect(task.maxIterations).toBe(5);
    expect(task.retry).toBe(2);
  });

  it('throws descriptive error for missing states', () => {
    const yaml = `
name: No States
start_at: first
`;
    expect(() => parseWorkflow(yaml)).toThrow(WorkflowParseError);
    expect(() => parseWorkflow(yaml)).toThrow("Missing required field 'states'");
  });

  it('throws descriptive error for missing start_at', () => {
    const yaml = `
name: No Start
states:
  first:
    type: task
    provider: system
    command: echo
`;
    expect(() => parseWorkflow(yaml)).toThrow(WorkflowParseError);
    expect(() => parseWorkflow(yaml)).toThrow("Missing required field 'start_at'");
  });

  it('ignores unknown fields in YAML without error', () => {
    const yaml = `
name: Unknown Fields
start_at: first
unknown_field: ignore me
another_unknown: 123
states:
  first:
    type: task
    provider: system
    command: echo
    custom_field: this is custom
    result_path: $.result
`;
    const workflow = parseWorkflow(yaml);
    expect(workflow.name).toBe('Unknown Fields');
    expect(workflow.states['first']).toBeDefined();
  });

  it('applies defaults retry 3 maxLoop 10 failFast true', () => {
    const yaml = `
name: Defaults Test
start_at: task_step
states:
  task_step:
    type: task
    provider: system
    command: echo
    result_path: $.result
  map_step:
    type: map
    items_path: $.items
    iterator:
      states:
        - name: inner
          type: task
          provider: system
          command: echo
          result_path: $.result
          retry: 0
    result_path: $.result
`;
    const workflow = parseWorkflow(yaml);

    const task = workflow.states['task_step'] as { retry: number };
    expect(task.retry).toBe(3);
    expect(workflow.maxLoop).toBe(10);

    const map = workflow.states['map_step'] as { failFast: boolean };
    expect(map.failFast).toBe(true);
  });

  it('handles end true states without next', () => {
    const yaml = `
name: End States
start_at: task1
states:
  task1:
    type: task
    provider: system
    command: echo done
    result_path: $.result
    end: true
`;
    const workflow = parseWorkflow(yaml);

    const task = workflow.states['task1'] as { end: boolean | null; next: string | null };
    expect(task.end).toBe(true);
    expect(task.next).toBe(null);
  });

  it('derives name from filePath when name field is missing', () => {
    const yaml = `
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
    result_path: $.result
`;
    const workflow = parseWorkflow(yaml, '/path/to/my_workflow.yaml');
    expect(workflow.name).toBe('my_workflow');
  });

  it('uses unnamed as fallback when no name and no filePath', () => {
    const yaml = `
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo
    result_path: $.result
`;
    const workflow = parseWorkflow(yaml);
    expect(workflow.name).toBe('unnamed');
  });

  it('handles profile field as provider fallback', () => {
    const yaml = `
name: Profile Test
start_at: task_step
states:
  task_step:
    type: task
    profile: smart_guy
    prompt_template: hello
    result_path: $.result
`;
    const workflow = parseWorkflow(yaml);

    const task = workflow.states['task_step'] as { provider: string };
    expect(task.provider).toBe('smart_guy');
  });

  it('throws when YAML content is not an object', () => {
    expect(() => parseWorkflow('just a string')).toThrow(WorkflowParseError);
    expect(() => parseWorkflow('123')).toThrow(WorkflowParseError);
  });

  it('throws when YAML parse fails', () => {
    expect(() => parseWorkflow('invalid: yaml: [')).toThrow(WorkflowParseError);
    expect(() => parseWorkflow('invalid: yaml: [')).toThrow(/Failed to parse YAML/);
  });
});
