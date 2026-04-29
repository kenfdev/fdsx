import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { GraphNode, ParallelState } from '../../../shared/types.js';
import styles from '../../styles/nodes.module.css';

export function ParallelNode({ data }: NodeProps<GraphNode>) {
  const state = data.state as ParallelState;
  const branchCount = state.branches.length;

  return (
    <>
      <Handle type="target" position={Position.Top} />
      <div className={`${styles.nodeBase} ${data.isStart ? styles.startNode : data.isEnd ? styles.endNode : ''}`}>
        <p className={styles.nodeLabel}>
          {data.isStart ? (
            <span className={styles.nodeIcon}>▶</span>
          ) : data.isEnd ? (
            <span className={styles.nodeIcon}>■</span>
          ) : null}
          {data.label}
        </p>
        <p className={styles.nodeSublabel}>{branchCount} branch{branchCount !== 1 ? 'es' : ''}</p>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}
