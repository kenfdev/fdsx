import yaml from 'js-yaml';
import type {
  Workflow,
  State,
  TaskState,
  ChoiceState,
  ParallelState,
  PassState,
  WaitState,
  MapState,
  Branch,
  ExtractRule,
  HookConfig,
  HookEntry,
  ChoiceRule,
  AggregateRule,
  IteratorTaskState,
  Strategy,
  Operator,
} from '../shared/types.js';

interface RawWorkflow {
  name?: string;
  description?: string;
  start_at?: string;
  states?: Record<string, unknown>;
  version?: string | null;
  max_loop?: number;
  providers?: Record<string, Record<string, unknown>> | null;
  hooks?: RawHookConfig | null;
  profiles?: Record<string, Record<string, unknown>> | null;
}

interface RawHookConfig {
  on_start?: RawHookEntry[] | null;
  on_complete?: RawHookEntry[] | null;
  on_failure?: RawHookEntry[] | null;
}

interface RawHookEntry {
  command?: string;
  on_failure?: string;
}

interface RawExtractRule {
  strategy?: Strategy[] | string[];
  pattern?: string;
  result_path?: string;
}

interface RawChoiceRule {
  variable?: string;
  operator?: Operator | string;
  value?: unknown;
  next?: string;
}

interface RawBranch {
  provider?: string;
  model?: string | null;
  prompt_template?: string | null;
  prompt_file?: string | null;
  command?: string | null;
  extract?: RawExtractRule | null;
  retry?: number;
  timeout_seconds?: number | null;
  provider_options?: Record<string, unknown> | null;
}

interface RawAggregateRule {
  source?: string;
  field?: string;
  strategy?: string;
  match?: string;
  no_match?: string;
  result_path?: string;
}

interface RawIteratorTaskState {
  type?: string;
  name?: string;
  provider?: string;
  model?: string | null;
  prompt_template?: string | null;
  prompt_file?: string | null;
  command?: string | null;
  result_path?: string;
  result_file?: string | null;
  extract?: RawExtractRule | null;
  retry?: number;
  timeout_seconds?: number | null;
  provider_options?: Record<string, unknown> | null;
}

interface RawPassState {
  type?: string;
  parameters?: Record<string, unknown> | null;
  aggregate?: RawAggregateRule | null;
  max_iterations?: number | null;
  hooks?: RawHookConfig | null;
  next?: string | null;
  end?: boolean | null;
}

interface RawWaitState {
  type?: string;
  mode?: string;
  message?: string;
  choices?: string[];
  result_path?: string;
  notify?: unknown | null;
  max_iterations?: number | null;
  hooks?: RawHookConfig | null;
  next?: string | null;
  end?: boolean | null;
}

function mapHookEntry(raw: RawHookEntry): HookEntry {
  return {
    command: raw.command ?? '',
    onFailure: (raw.on_failure === 'abort' ? 'abort' : 'warn') as HookEntry['onFailure'],
  };
}

function mapHookConfig(raw: RawHookConfig | null | undefined): HookConfig | null {
  if (!raw) return null;
  return {
    onStart: raw.on_start?.map(mapHookEntry) ?? [],
    onComplete: raw.on_complete?.map(mapHookEntry) ?? [],
    onFailure: raw.on_failure?.map(mapHookEntry) ?? [],
  };
}

function mapExtractRule(raw: RawExtractRule | null | undefined): ExtractRule | null {
  if (!raw) return null;
  const strategy: Strategy[] = (raw.strategy ?? []).map((s) =>
    typeof s === 'string' ? s : s,
  ) as Strategy[];
  return {
    strategy,
    pattern: raw.pattern ?? '',
    resultPath: raw.result_path ?? '',
  };
}

function mapChoiceRule(raw: RawChoiceRule): ChoiceRule {
  return {
    variable: raw.variable ?? '',
    operator: (raw.operator ?? 'equals') as Operator,
    value: raw.value,
    next: raw.next ?? '',
  };
}

function mapAggregateRule(raw: RawAggregateRule | null | undefined): AggregateRule | null {
  if (!raw) return null;
  return {
    source: raw.source ?? '',
    field: raw.field ?? '',
    strategy: raw.strategy ?? '',
    match: raw.match ?? '',
    noMatch: raw.no_match ?? '',
    resultPath: raw.result_path ?? '',
  };
}

function mapBranch(raw: RawBranch): Branch {
  return {
    provider: raw.provider ?? '',
    model: raw.model ?? null,
    promptTemplate: raw.prompt_template ?? null,
    promptFile: raw.prompt_file ?? null,
    command: raw.command ?? null,
    extract: mapExtractRule(raw.extract),
    retry: raw.retry ?? 3,
    timeoutSeconds: raw.timeout_seconds ?? null,
    providerOptions: raw.provider_options ?? null,
  };
}

function mapIteratorTaskState(raw: RawIteratorTaskState): IteratorTaskState {
  return {
    type: 'task',
    name: raw.name ?? '',
    provider: raw.provider ?? '',
    model: raw.model ?? null,
    promptTemplate: raw.prompt_template ?? null,
    promptFile: raw.prompt_file ?? null,
    command: raw.command ?? null,
    resultPath: raw.result_path ?? '',
    resultFile: raw.result_file ?? null,
    extract: mapExtractRule(raw.extract),
    retry: raw.retry ?? 3,
    timeoutSeconds: raw.timeout_seconds ?? null,
    providerOptions: raw.provider_options ?? null,
  };
}

function mapTaskState(raw: Record<string, unknown>, stateName: string): TaskState {
  if (!raw.result_path && typeof raw.result_path !== 'string') {
    throw new WorkflowParseError(
      `Missing required field 'result_path' in task state '${stateName}'`,
    );
  }
  return {
    type: 'task',
    provider: (raw.provider as string) ?? (raw.profile as string) ?? '',
    model: (raw.model as string | null) ?? null,
    promptTemplate: (raw.prompt_template as string | null) ?? null,
    promptFile: (raw.prompt_file as string | null) ?? null,
    command: (raw.command as string | null) ?? null,
    resultPath: (raw.result_path as string) ?? '',
    resultFile: (raw.result_file as string | null) ?? null,
    extract: mapExtractRule(raw.extract as RawExtractRule | undefined),
    maxIterations: (raw.max_iterations as number | null) ?? null,
    retry: (raw.retry as number) ?? 3,
    timeoutSeconds: (raw.timeout_seconds as number | null) ?? null,
    providerOptions: (raw.provider_options as Record<string, unknown> | null) ?? null,
    hooks: mapHookConfig(raw.hooks as RawHookConfig | undefined),
    next: (raw.next as string | null) ?? null,
    end: (raw.end as boolean | null) ?? null,
  };
}

function mapChoiceState(raw: Record<string, unknown>): ChoiceState {
  const choicesRaw = (raw.choices as RawChoiceRule[] | undefined) ?? [];
  return {
    type: 'choice',
    choices: choicesRaw.map(mapChoiceRule),
    default: (raw.default as string | null) ?? null,
    maxIterations: (raw.max_iterations as number | null) ?? null,
    hooks: mapHookConfig(raw.hooks as RawHookConfig | undefined),
  };
}

function mapParallelState(raw: Record<string, unknown>, stateName: string): ParallelState {
  if (!raw.result_path && typeof raw.result_path !== 'string') {
    throw new WorkflowParseError(
      `Missing required field 'result_path' in parallel state '${stateName}'`,
    );
  }
  const branchesRaw = (raw.branches as RawBranch[] | undefined) ?? [];
  return {
    type: 'parallel',
    branches: branchesRaw.map(mapBranch),
    resultPath: (raw.result_path as string) ?? '',
    resultFile: (raw.result_file as string | null) ?? null,
    minSuccess: (raw.min_success as number | null) ?? null,
    maxIterations: (raw.max_iterations as number | null) ?? null,
    hooks: mapHookConfig(raw.hooks as RawHookConfig | undefined),
    next: (raw.next as string | null) ?? null,
    end: (raw.end as boolean | null) ?? null,
  };
}

function mapPassState(raw: Record<string, unknown>): PassState {
  return {
    type: 'pass',
    parameters: (raw.parameters as Record<string, unknown> | null) ?? null,
    aggregate: mapAggregateRule(raw.aggregate as RawAggregateRule | undefined),
    maxIterations: (raw.max_iterations as number | null) ?? null,
    hooks: mapHookConfig(raw.hooks as RawHookConfig | undefined),
    next: (raw.next as string | null) ?? null,
    end: (raw.end as boolean | null) ?? null,
  };
}

function mapWaitState(raw: Record<string, unknown>, stateName: string): WaitState {
  if (!raw.result_path && typeof raw.result_path !== 'string') {
    throw new WorkflowParseError(
      `Missing required field 'result_path' in wait state '${stateName}'`,
    );
  }
  const notifyRaw = raw.notify as Record<string, unknown> | undefined;
  const notifyValue: { webhook: { url: string; template: string } } | null =
    notifyRaw && typeof notifyRaw === 'object'
      ? {
          webhook: {
            url: (notifyRaw.webhook as { url?: string; template?: string } | undefined)?.url ?? '',
            template:
              (notifyRaw.webhook as { url?: string; template?: string } | undefined)?.template ?? '',
          },
        }
      : null;

  return {
    type: 'wait',
    mode: (raw.mode as string) ?? 'prompt',
    message: (raw.message as string) ?? '',
    choices: (raw.choices as string[] | undefined) ?? [],
    resultPath: (raw.result_path as string) ?? '',
    notify: notifyValue,
    maxIterations: (raw.max_iterations as number | null) ?? null,
    hooks: mapHookConfig(raw.hooks as RawHookConfig | undefined),
    next: (raw.next as string | null) ?? null,
    end: (raw.end as boolean | null) ?? null,
  };
}

function mapMapState(raw: Record<string, unknown>, stateName: string): MapState {
  if (!raw.items_path && typeof raw.items_path !== 'string') {
    throw new WorkflowParseError(
      `Missing required field 'items_path' in map state '${stateName}'`,
    );
  }
  if (!raw.result_path && typeof raw.result_path !== 'string') {
    throw new WorkflowParseError(
      `Missing required field 'result_path' in map state '${stateName}'`,
    );
  }
  const iteratorRaw = (raw.iterator as { states?: RawIteratorTaskState[] } | undefined);
  const iteratorStates = (iteratorRaw?.states as RawIteratorTaskState[] | undefined) ?? [];

  return {
    type: 'map',
    itemsPath: (raw.items_path as string) ?? '',
    iterator: {
      states: iteratorStates.map(mapIteratorTaskState),
    },
    resultPath: (raw.result_path as string) ?? '',
    failFast: raw.fail_fast !== false,
    maxIterations: (raw.max_iterations as number | null) ?? null,
    hooks: mapHookConfig(raw.hooks as RawHookConfig | undefined),
    next: (raw.next as string | null) ?? null,
    end: (raw.end as boolean | null) ?? null,
  };
}

function mapState(raw: Record<string, unknown>, stateName: string): State | null {
  const type = raw.type as string;

  switch (type) {
    case 'task':
      return mapTaskState(raw, stateName);
    case 'choice':
      return mapChoiceState(raw);
    case 'parallel':
      return mapParallelState(raw, stateName);
    case 'pass':
      return mapPassState(raw);
    case 'wait':
      return mapWaitState(raw, stateName);
    case 'map':
      return mapMapState(raw, stateName);
    default:
      return null;
  }
}

export class WorkflowParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WorkflowParseError';
  }
}

export function parseWorkflow(yamlContent: string, filePath?: string): Workflow {
  let parsed: unknown;
  try {
    parsed = yaml.load(yamlContent);
  } catch (err) {
    throw new WorkflowParseError(
      `Failed to parse YAML: ${err instanceof Error ? err.message : String(err)}`,
    );
  }

  if (typeof parsed !== 'object' || parsed === null) {
    throw new WorkflowParseError('YAML content must be an object');
  }

  const workflow = parsed as RawWorkflow;

  if (!workflow.start_at) {
    throw new WorkflowParseError("Missing required field 'start_at' in workflow");
  }

  if (!workflow.states) {
    throw new WorkflowParseError("Missing required field 'states' in workflow");
  }

  const name =
    workflow.name ??
    (filePath ? filePath.split('/').pop()?.replace(/\.(yaml|yml)$/, '') ?? 'unnamed' : 'unnamed');

  const states: Record<string, State> = {};
  for (const [stateName, stateRaw] of Object.entries(workflow.states ?? {})) {
    if (typeof stateRaw === 'object' && stateRaw !== null) {
      const mapped = mapState(stateRaw as Record<string, unknown>, stateName);
      if (mapped !== null) {
        states[stateName] = mapped;
      }
    }
  }

  return {
    name,
    description: workflow.description ?? '',
    startAt: workflow.start_at,
    states,
    version: workflow.version ?? null,
    maxLoop: workflow.max_loop ?? 10,
    providers: workflow.providers ?? null,
    hooks: mapHookConfig(workflow.hooks),
    profiles: workflow.profiles ?? null,
  };
}
