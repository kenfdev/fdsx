import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { GraphNode } from '../../../shared/types.js';
import styles from '../../styles/nodes.module.css';

export function ChoiceNode({ data }: NodeProps<GraphNode>) {
  return (
    <>
      <Handle type="target" position={Position.Top} />
      <div
        className={`${styles.diamondOuter} ${data.isStart ? styles.startNode : data.isEnd ? styles.endNode : ''}`}
      >
        <div className={styles.diamondInner}>
          <p className={styles.nodeLabel}>
            {data.isStart ? (
              <span className={styles.nodeIcon}>▶</span>
            ) : data.isEnd ? (
              <span className={styles.nodeIcon}>■</span>
            ) : null}
            {data.label}
          </p>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} />
    </>
  );
}
