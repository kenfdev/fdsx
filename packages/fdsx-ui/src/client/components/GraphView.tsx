import { useEffect, useState, useCallback } from 'react';
import * as ReactFlowModule from '@xyflow/react';
import { Background, Controls, useNodesState, useEdgesState } from '@xyflow/react';
import type { Node, Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';

const ReactFlow = ReactFlowModule.ReactFlow;
import type { GraphNode, GraphEdge } from '../../shared/types.js';
import { nodeTypes } from './nodes/index.js';
import { NodeDetail } from './NodeDetail.js';
import styles from '../styles/GraphView.module.css';

interface WorkflowResponse {
  workflow: {
    name: string;
    description: string;
    startAt: string;
  };
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface GraphViewProps {
  workflowPath: string;
}

export function GraphView({ workflowPath }: GraphViewProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [workflowName, setWorkflowName] = useState<string | null>(null);

  const fetchWorkflow = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelectedNode(null);
    try {
      const res = await fetch(`/api/workflows/${workflowPath}`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Failed to fetch workflow: ${res.status}`);
      }
      const data: WorkflowResponse = await res.json();
      setNodes(data.nodes as Node[]);
      setEdges(data.edges as Edge[]);
      setWorkflowName(data.workflow.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [workflowPath, setNodes, setEdges]);

  const reloadWorkflow = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSelectedNode(null);
    try {
      const res = await fetch(`/api/workflows/${workflowPath}/reload`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Failed to reload workflow: ${res.status}`);
      }
      const data: WorkflowResponse = await res.json();
      setNodes(data.nodes as Node[]);
      setEdges(data.edges as Edge[]);
      setWorkflowName(data.workflow.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, [workflowPath, setNodes, setEdges]);

  useEffect(() => {
    fetchWorkflow();
  }, [fetchWorkflow]);

  const handleReload = useCallback(() => {
    reloadWorkflow();
  }, [reloadWorkflow]);

  const handleNodeClick = useCallback((_: unknown, node: Node) => {
    setSelectedNode(node as GraphNode);
  }, []);

  const handleCloseNodeDetail = useCallback(() => {
    setSelectedNode(null);
  }, []);

  if (loading) {
    return (
      <div className={styles.container}>
        <div className={styles.message}>Loading workflow...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={styles.container}>
        <div className={styles.error}>
          <p className={styles.errorMessage}>{error}</p>
          <button type="button" className={styles.retryButton} onClick={handleReload}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className={styles.container}>
        <div className={styles.message}>This workflow has no states defined.</div>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      <div className={styles.toolbar}>
        <span className={styles.workflowName}>{workflowName || workflowPath}</span>
        <button type="button" className={styles.reloadButton} onClick={handleReload}>
          Reload
        </button>
      </div>
      <div className={styles.flowContainer}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={handleNodeClick}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>
      {selectedNode && (
        <NodeDetail node={selectedNode} onClose={handleCloseNodeDetail} />
      )}
    </div>
  );
}
