import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { GraphNode } from '../../../shared/types.js';
import styles from '../../styles/nodes.module.css';

export function PassNode({ data }: NodeProps<GraphNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <div className={`${styles.nodeBase} ${styles.passNode} ${data.isStart ? styles.startNode : ''}`}>
        <p className={styles.nodeLabel}>{data.label}</p>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}
