import styles from '../styles/EmptyState.module.css';

interface EmptyStateProps {
  type: 'no-workflows' | 'no-selection';
}

export function EmptyState({ type }: EmptyStateProps) {
  if (type === 'no-workflows') {
    return (
      <div className={styles.container}>
        <div className={styles.icon}>📋</div>
        <h2 className={styles.title}>No workflows found</h2>
        <p className={styles.description}>
          Check your workflow directory path or create a new workflow file.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      <div className={styles.icon}>👈</div>
      <h2 className={styles.title}>Select a workflow</h2>
      <p className={styles.description}>
        Click on a workflow from the sidebar to view its graph.
      </p>
    </div>
  );
}
