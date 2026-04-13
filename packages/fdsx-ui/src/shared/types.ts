import type { Node, Edge } from '@xyflow/react';

export type Operator = 'equals' | 'not_equals' | 'greater_than' | 'less_than' | 'contains';

export type ProviderType = 'claude' | 'opencode' | 'codex' | 'gemini' | 'system';

export type Strategy = 'json' | 'regex' | 'keyword';

export interface LLMClassifyFallback {
  type: 'llm_classify';
  provider: string;
  prompt: string;
}

export interface ExtractRule {
  strategy: Strategy[];
  pattern: string;
  resultPath: string;
}

export interface WebhookConfig {
  url: string;
  template: string;
}

export interface NotifyConfig {
  webhook: WebhookConfig;
}

export interface HookEntry {
  command: string;
  onFailure: 'abort' | 'warn';
}

export interface HookConfig {
  onStart: HookEntry[];
  onComplete: HookEntry[];
  onFailure: HookEntry[];
}

export interface ChoiceRule {
  variable: string;
  operator: Operator;
  value: unknown;
  next: string;
}

export type ProfileConfig = Record<string, unknown>;
export type ProviderConfig = Record<string, unknown>;

export interface Branch {
  provider: string;
  model: string | null;
  promptTemplate: string | null;
  promptFile: string | null;
  command: string | null;
  extract: ExtractRule | null;
  retry: number;
  timeoutSeconds: number | null;
  providerOptions: Record<string, unknown> | null;
}

export interface AggregateRule {
  source: string;
  field: string;
  strategy: string;
  match: string;
  noMatch: string;
  resultPath: string;
}

export interface TaskState {
  type: 'task';
  provider: string;
  model: string | null;
  promptTemplate: string | null;
  promptFile: string | null;
  command: string | null;
  resultPath: string;
  resultFile: string | null;
  extract: ExtractRule | null;
  maxIterations: number | null;
  retry: number;
  timeoutSeconds: number | null;
  providerOptions: Record<string, unknown> | null;
  hooks: HookConfig | null;
  next: string | null;
  end: boolean | null;
}

export interface ChoiceState {
  type: 'choice';
  choices: ChoiceRule[];
  default: string | null;
  maxIterations: number | null;
  hooks: HookConfig | null;
}

export interface ParallelState {
  type: 'parallel';
  branches: Branch[];
  resultPath: string;
  resultFile: string | null;
  minSuccess: number | null;
  maxIterations: number | null;
  hooks: HookConfig | null;
  next: string | null;
  end: boolean | null;
}

export interface PassState {
  type: 'pass';
  parameters: Record<string, unknown> | null;
  aggregate: AggregateRule | null;
  maxIterations: number | null;
  hooks: HookConfig | null;
  next: string | null;
  end: boolean | null;
}

export interface WaitState {
  type: 'wait';
  mode: string;
  message: string;
  choices: string[];
  resultPath: string;
  notify: NotifyConfig | null;
  maxIterations: number | null;
  hooks: HookConfig | null;
  next: string | null;
  end: boolean | null;
}

export interface IteratorTaskState {
  type: 'task';
  name: string;
  provider: string;
  model: string | null;
  promptTemplate: string | null;
  promptFile: string | null;
  command: string | null;
  resultPath: string;
  resultFile: string | null;
  extract: ExtractRule | null;
  retry: number;
  timeoutSeconds: number | null;
  providerOptions: Record<string, unknown> | null;
}

export interface IteratorDef {
  states: IteratorTaskState[];
}

export interface MapState {
  type: 'map';
  itemsPath: string;
  iterator: IteratorDef;
  resultPath: string;
  failFast: boolean;
  maxIterations: number | null;
  hooks: HookConfig | null;
  next: string | null;
  end: boolean | null;
}

export type State = TaskState | ChoiceState | ParallelState | PassState | WaitState | MapState;

export interface Workflow {
  name: string;
  description: string;
  startAt: string;
  states: Record<string, State>;
  version: string | null;
  maxLoop: number;
  providers: Record<string, ProviderConfig> | null;
  hooks: HookConfig | null;
  profiles: Record<string, ProfileConfig> | null;
}

export interface WorkflowFile {
  name: string;
  filePath: string;
  relativePath: string;
}

export interface NodeData extends Record<string, unknown> {
  label: string;
  stateType: State['type'];
  state: State;
  isStart: boolean;
}

export type GraphNode = Node<NodeData>;
export type GraphEdge = Edge & {
  label?: string | null;
  edgeType?: 'normal' | 'choice' | 'parallel' | 'loop';
  points?: Array<{ x: number; y: number }>;
};
