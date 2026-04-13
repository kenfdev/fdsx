import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { GraphNode, TaskState } from '../../../shared/types.js';
import styles from '../../styles/nodes.module.css';

export function TaskNode({ data }: NodeProps<GraphNode>) {
  const state = data.state as TaskState;
  const provider = state.provider;

  return (
    <>
      <Handle type="target" position={Position.Top} />
      <div className={`${styles.nodeBase} ${data.isStart ? styles.startNode : ''}`}>
        <p className={styles.nodeLabel}>{data.label}</p>
        <p className={styles.nodeSublabel}>{provider}</p>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}
