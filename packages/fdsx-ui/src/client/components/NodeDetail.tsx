import type { GraphNode, GraphEdge, State, TaskState, ChoiceState, ParallelState, MapState, WaitState, PassState } from '../../shared/types.js';
import styles from '../styles/NodeDetail.module.css';

interface NodeDetailProps {
  node: GraphNode;
  onClose: () => void;
}

function renderTaskState(state: TaskState) {
  return (
    <>
      <div className={styles.section}>
        <h4 className={styles.sectionTitle}>Provider</h4>
        <p className={styles.field}>{state.provider}</p>
        {state.model && (
          <>
            <h4 className={styles.sectionTitle}>Model</h4>
            <p className={styles.field}>{state.model}</p>
          </>
        )}
      </div>
      {(state.promptTemplate || state.promptFile) && (
        <div className={styles.section}>
          <h4 className={styles.sectionTitle}>Prompt</h4>
          <pre className={styles.codeBlock}>
            {state.promptTemplate || `[File: ${state.promptFile}]`}
          </pre>
        </div>
      )}
      {state.command && (
        <div className={styles.section}>
          <h4 className={styles.sectionTitle}>Command</h4>
          <p className={styles.field}>{state.command}</p>
        </div>
      )}
    </>
  );
}

function renderChoiceState(state: ChoiceState) {
  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>Choices</h4>
      <ul className={styles.choiceList}>
        {state.choices.map((choice, i) => (
          <li key={i} className={styles.choiceItem}>
            <code className={styles.choiceCondition}>
              {choice.variable} {choice.operator} {JSON.stringify(choice.value)}
            </code>
            <span className={styles.choiceArrow}>→</span>
            <span className={styles.choiceNext}>{choice.next}</span>
          </li>
        ))}
        {state.default && (
          <li className={styles.choiceItem}>
            <span className={styles.choiceDefault}>default</span>
            <span className={styles.choiceArrow}>→</span>
            <span className={styles.choiceNext}>{state.default}</span>
          </li>
        )}
      </ul>
    </div>
  );
}

function renderParallelState(state: ParallelState) {
  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>Branches</h4>
      <ul className={styles.branchList}>
        {state.branches.map((branch, i) => (
          <li key={i} className={styles.branchItem}>
            <p className={styles.branchProvider}>{branch.provider}</p>
            {branch.promptTemplate && (
              <p className={styles.branchPrompt}>
                {branch.promptTemplate.length > 80
                  ? branch.promptTemplate.slice(0, 80) + '...'
                  : branch.promptTemplate}
              </p>
            )}
            {branch.promptFile && (
              <p className={styles.branchPrompt}>[File: {branch.promptFile}]</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function renderMapState(state: MapState) {
  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>Iterator</h4>
      <p className={styles.field}>
        <strong>Items Path:</strong> {state.itemsPath}
      </p>
      <p className={styles.field}>
        <strong>States:</strong> {state.iterator.states.map(s => s.name).join(', ')}
      </p>
      <p className={styles.field}>
        <strong>Fail Fast:</strong> {state.failFast ? 'Yes' : 'No'}
      </p>
    </div>
  );
}

function renderWaitState(state: WaitState) {
  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>Wait Configuration</h4>
      <p className={styles.field}>
        <strong>Mode:</strong> {state.mode}
      </p>
      {state.message && (
        <p className={styles.field}>
          <strong>Message:</strong> {state.message}
        </p>
      )}
      {state.choices.length > 0 && (
        <>
          <h4 className={styles.sectionTitle}>Choices</h4>
          <ul className={styles.choiceList}>
            {state.choices.map((choice, i) => (
              <li key={i} className={styles.choiceItem}>
                <span className={styles.choiceNext}>{choice}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function renderPassState(state: PassState) {
  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>Parameters</h4>
      {state.parameters ? (
        <pre className={styles.codeBlock}>{JSON.stringify(state.parameters, null, 2)}</pre>
      ) : (
        <p className={styles.field}>None</p>
      )}
    </div>
  );
}

function renderStateSpecificSection(state: State) {
  switch (state.type) {
    case 'task':
      return renderTaskState(state);
    case 'choice':
      return renderChoiceState(state);
    case 'parallel':
      return renderParallelState(state);
    case 'map':
      return renderMapState(state);
    case 'wait':
      return renderWaitState(state);
    case 'pass':
      return renderPassState(state);
    default:
      return null;
  }
}

function renderCommonFields(state: State) {
  const fields: Array<{ label: string; value: string }> = [];

  if ('resultPath' in state && state.resultPath) {
    fields.push({ label: 'Result Path', value: state.resultPath });
  }
  if ('retry' in state && state.retry > 0) {
    fields.push({ label: 'Retry', value: String(state.retry) });
  }
  if ('timeoutSeconds' in state && state.timeoutSeconds) {
    fields.push({ label: 'Timeout', value: `${state.timeoutSeconds}s` });
  }
  if ('providerOptions' in state && state.providerOptions) {
    fields.push({ label: 'Provider Options', value: JSON.stringify(state.providerOptions) });
  }

  if (fields.length === 0) return null;

  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>Options</h4>
      {fields.map((field, i) => (
        <p key={i} className={styles.field}>
          <strong>{field.label}:</strong> {field.value}
        </p>
      ))}
    </div>
  );
}

function renderTransitions(state: State) {
  const next = 'next' in state ? state.next : null;
  const end = 'end' in state ? state.end : null;

  if (!next && !end) return null;

  return (
    <div className={styles.section}>
      <h4 className={styles.sectionTitle}>Transitions</h4>
      {next && (
        <p className={styles.field}>
          <strong>Next:</strong> {next}
        </p>
      )}
      {end !== null && (
        <p className={styles.field}>
          <strong>End:</strong> {end ? 'Yes' : 'No'}
        </p>
      )}
    </div>
  );
}

export function NodeDetail({ node, onClose }: NodeDetailProps) {
  const state = node.data.state as State;
  const stateType = node.data.stateType;

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <div className={styles.headerContent}>
          <h2 className={styles.nodeName}>{node.data.label}</h2>
          <span className={styles.typeBadge}>{stateType}</span>
        </div>
        <button type="button" className={styles.closeButton} onClick={onClose}>
          ×
        </button>
      </div>
      <div className={styles.content}>
        {renderStateSpecificSection(state)}
        {renderCommonFields(state)}
        {renderTransitions(state)}
      </div>
    </div>
  );
}
