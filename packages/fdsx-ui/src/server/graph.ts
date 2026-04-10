import dagre from '@dagrejs/dagre';
import type { Workflow, GraphNode, GraphEdge, NodeData } from '../shared/types.js';

const DEFAULT_NODE_WIDTH = 180;
const DEFAULT_NODE_HEIGHT = 60;

export interface TransformResult {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

function createGraphNode(workflow: Workflow, stateName: string): GraphNode {
  const state = workflow.states[stateName];
  const nodeData: NodeData = {
    label: stateName,
    stateType: state.type,
    state,
    isStart: stateName === workflow.startAt,
  };

  return {
    id: stateName,
    type: state.type,
    data: nodeData,
    position: { x: 0, y: 0 },
  };
}

function createGraphEdge(
  source: string,
  target: string,
  label?: string,
  edgeType?: GraphEdge['edgeType'],
): GraphEdge {
  const edge: GraphEdge = {
    id: `${source}->${target}`,
    source,
    target,
  };

  if (label) {
    edge.label = label;
  }
  if (edgeType) {
    edge.edgeType = edgeType;
  }

  return edge;
}

export function transformWorkflow(workflow: Workflow): TransformResult {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];

  for (const stateName of Object.keys(workflow.states)) {
    nodes.push(createGraphNode(workflow, stateName));
  }

  for (const [stateName, state] of Object.entries(workflow.states)) {
    switch (state.type) {
      case 'task': {
        if (state.next && !state.end) {
          edges.push(createGraphEdge(stateName, state.next));
        }
        break;
      }
      case 'choice': {
        for (let i = 0; i < state.choices.length; i++) {
          const choice = state.choices[i];
          const edgeId = `${stateName}->${choice.next}:${i}`;
          edges.push({
            id: edgeId,
            source: stateName,
            target: choice.next,
            label: `${choice.variable} ${choice.operator} ${String(choice.value)}`,
            edgeType: 'choice',
          });
        }
        if (state.default) {
          edges.push(createGraphEdge(stateName, state.default, 'default', 'choice'));
        }
        break;
      }
      case 'parallel': {
        if (state.next && !state.end) {
          edges.push(createGraphEdge(stateName, state.next, undefined, 'parallel'));
        }
        break;
      }
      case 'pass': {
        if (state.next && !state.end) {
          edges.push(createGraphEdge(stateName, state.next));
        }
        break;
      }
      case 'wait': {
        if (state.next && !state.end) {
          edges.push(createGraphEdge(stateName, state.next));
        }
        break;
      }
      case 'map': {
        if (state.next && !state.end) {
          edges.push(createGraphEdge(stateName, state.next));
        }
        break;
      }
    }
  }

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 80 });

  for (const node of nodes) {
    g.setNode(node.id, { width: DEFAULT_NODE_WIDTH, height: DEFAULT_NODE_HEIGHT });
  }

  for (const edge of edges) {
    g.setEdge(edge.source, edge.target);
  }

  dagre.layout(g);

  for (const node of nodes) {
    const layoutNode = g.node(node.id);
    if (layoutNode) {
      node.position = {
        x: layoutNode.x - DEFAULT_NODE_WIDTH / 2,
        y: layoutNode.y - DEFAULT_NODE_HEIGHT / 2,
      };
    }
  }

  for (const edge of edges) {
    const layoutEdge = g.edge({ v: edge.source, w: edge.target });
    if (layoutEdge && layoutEdge.points) {
      edge.points = layoutEdge.points;
    }
  }

  return { nodes, edges };
}
