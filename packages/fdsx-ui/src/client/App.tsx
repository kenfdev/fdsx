import { useState, useCallback, useEffect } from 'react';
import { WorkflowList } from './components/WorkflowList.js';
import type { WorkflowFile } from '../shared/types.js';
import styles from './styles/App.module.css';

function readSelectedWorkflow(): string | null {
  const params = new URLSearchParams(window.location.search);
  return params.get('workflow');
}

function writeSelectedWorkflow(path: string): void {
  const params = new URLSearchParams(window.location.search);
  params.set('workflow', path);
  history.pushState(null, '', `?${params.toString()}`);
}

export function App() {
  const [selectedWorkflow, setSelectedWorkflowState] = useState<WorkflowFile | null>(
    () => {
      const initial = readSelectedWorkflow();
      return initial ? { name: '', filePath: '', relativePath: initial } : null;
    },
  );

  const handleSelectWorkflow = useCallback((workflow: WorkflowFile) => {
    writeSelectedWorkflow(workflow.relativePath);
    setSelectedWorkflowState(workflow);
  }, []);

  useEffect(() => {
    const handlePopState = () => {
      const path = readSelectedWorkflow();
      if (path) {
        setSelectedWorkflowState({ name: '', filePath: '', relativePath: path });
      } else {
        setSelectedWorkflowState(null);
      }
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  return (
    <div className={styles.layout}>
      <aside className={styles.sidebar}>
        <WorkflowList selectedWorkflow={selectedWorkflow} onSelect={handleSelectWorkflow} />
      </aside>
      <main className={styles.main}>
        {selectedWorkflow ? (
          <div className={styles.graphArea}>
            <p>Workflow: {selectedWorkflow.relativePath}</p>
          </div>
        ) : (
          <div className={styles.placeholder}>
            <p>Select a workflow from the sidebar</p>
          </div>
        )}
      </main>
    </div>
  );
}
