import { useEffect, useState } from 'react';
import type { WorkflowFile } from '../../shared/types.js';
import styles from '../styles/WorkflowList.module.css';

interface WorkflowListProps {
  selectedWorkflow: WorkflowFile | null;
  onSelect: (workflow: WorkflowFile) => void;
}

export function WorkflowList({ selectedWorkflow, onSelect }: WorkflowListProps) {
  const [workflows, setWorkflows] = useState<WorkflowFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchWorkflows() {
      try {
        const res = await fetch('/api/workflows');
        if (!res.ok) {
          throw new Error(`Failed to fetch workflows: ${res.status}`);
        }
        const data: WorkflowFile[] = await res.json();
        const sorted = [...data].sort((a, b) => a.relativePath.localeCompare(b.relativePath));
        setWorkflows(sorted);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchWorkflows();
  }, []);

  return (
    <div className={styles.container}>
      <h2 className={styles.heading}>Workflows</h2>
      {loading && <p className={styles.message}>Loading...</p>}
      {error && <p className={styles.error}>{error}</p>}
      {!loading && !error && workflows.length === 0 && (
        <p className={styles.message}>No workflows found</p>
      )}
      <ul className={styles.list}>
        {workflows.map((wf) => {
          const isActive = selectedWorkflow?.relativePath === wf.relativePath;
          return (
            <li key={wf.relativePath}>
              <button
                type="button"
                className={`${styles.item} ${isActive ? styles.active : ''}`}
                onClick={() => onSelect(wf)}
              >
                <span className={styles.name}>{wf.name}</span>
                <span className={styles.path}>{wf.relativePath}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
