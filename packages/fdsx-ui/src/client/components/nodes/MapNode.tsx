import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { GraphNode } from '../../../shared/types.js';
import styles from '../../styles/nodes.module.css';

export function MapNode({ data }: NodeProps<GraphNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <div className={`${styles.nodeBase} ${styles.mapNode} ${data.isStart ? styles.startNode : data.isEnd ? styles.endNode : ''}`}>
        <p className={styles.nodeLabel}>
          {data.isStart ? (
            <span className={styles.nodeIcon}>▶</span>
          ) : data.isEnd ? (
            <span className={styles.nodeIcon}>■</span>
          ) : null}
          {data.label}
        </p>
        <p className={styles.nodeSublabel}>iterator</p>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}
