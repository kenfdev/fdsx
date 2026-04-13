import { TaskNode } from './TaskNode.js';
import { ChoiceNode } from './ChoiceNode.js';
import { ParallelNode } from './ParallelNode.js';
import { PassNode } from './PassNode.js';
import { WaitNode } from './WaitNode.js';
import { MapNode } from './MapNode.js';

export const nodeTypes = {
  task: TaskNode,
  choice: ChoiceNode,
  parallel: ParallelNode,
  pass: PassNode,
  wait: WaitNode,
  map: MapNode,
};
