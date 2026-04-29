import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { GraphNode, WaitState } from '../../../shared/types.js';
import styles from '../../styles/nodes.module.css';

export function WaitNode({ data }: NodeProps<GraphNode>) {
  const state = data.state as WaitState;
  const mode = state.mode;

  return (
    <>
      <Handle type="target" position={Position.Top} />
      <div className={`${styles.nodeBase} ${styles.waitNode} ${data.isStart ? styles.startNode : data.isEnd ? styles.endNode : ''}`}>
        <p className={styles.nodeLabel}>
          {data.isStart ? (
            <span className={styles.nodeIcon}>▶</span>
          ) : data.isEnd ? (
            <span className={styles.nodeIcon}>■</span>
          ) : null}
          {data.label}
        </p>
        <p className={styles.nodeSublabel}>{mode}</p>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}
