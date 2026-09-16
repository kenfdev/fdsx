"""Engine package re-export facade.

All public and tested-private symbols are re-exported here so that
existing imports like ``from fdsx.core.engine import run_flow`` continue
to work without changes.
"""

from .errors import (
    CheckpointNotFoundError,
    EngineError,
    FlowExecutionError,
    RunLockedError,
)
from .results import (
    AbortInfo,
    FlowResult,
    _calc_elapsed,
    _detect_abort_status,
    _extract_results,
    _find_failed_state,
    _sanitize_state_for_log,
)
from .resume import resume_flow
from .run import run_flow
from .tasks_dir import (
    _filter_actionable_entries,
    _update_task_status,
    _workflow_persist_id,
    load_tasks_dir,
    run_tasks_dir,
)
from .validate import FailStateTermination, FlowValidationError, validate_flow

__all__ = [
    "AbortInfo",
    "CheckpointNotFoundError",
    "EngineError",
    "FailStateTermination",
    "FlowExecutionError",
    "FlowResult",
    "FlowValidationError",
    "RunLockedError",
    "_calc_elapsed",
    "_detect_abort_status",
    "_extract_results",
    "_filter_actionable_entries",
    "_find_failed_state",
    "_sanitize_state_for_log",
    "_update_task_status",
    "_workflow_persist_id",
    "load_tasks_dir",
    "resume_flow",
    "run_flow",
    "run_tasks_dir",
    "validate_flow",
]
