import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { GraphNode } from '../../../shared/types.js';
import styles from '../../styles/nodes.module.css';

export function ChoiceNode({ data }: NodeProps<GraphNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <div
        className={`${styles.nodeBase} ${styles.diamondWrapper} ${data.isStart ? styles.startNode : ''}`}
      >
        <div className={styles.diamondContent}>
          <p className={styles.nodeLabel}>{data.label}</p>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}
