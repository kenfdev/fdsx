"""StreamLogger: real-time provider output streaming with per-state log files.

Streams provider stdout/stderr to the terminal's stderr with a ``[state_name]``
prefix, and writes complete output to a per-state log file under log_dir.

Design notes:
- ANSI escape codes pass through as-is (no sanitization) per FR-2.7.
- Log files are created lazily on the first line of output per FR-2.6.
- Log directory is created with mode 0o700 consistent with runs directory.
- File writes are protected by a per-instance lock; for parallel branches that
  share a log file, each branch opens its own file handle in append ("a") mode.
  Line-level OS append writes are atomic for typical log line sizes.
"""

import os
import sys
import threading
from pathlib import Path
from typing import IO

LOG_FILE_SUFFIX = ".log"


class StreamLogger:
    """Streams provider output to terminal (stderr) with state-name prefix.

    Writes complete per-state output to a log file under log_dir.

    Args:
        state_name: Name of the state (or parallel state for branches).
                    Used as the terminal prefix ``[state_name]`` and as the
                    log file stem ``<state_name>.log``.
        log_dir: Directory for per-state log files. When None, terminal
                 streaming still works but no log file is written.
        quiet: When True, suppresses print to stderr. Log file writes are
               unaffected.
    """

    def __init__(
        self,
        state_name: str,
        log_dir: Path | None = None,
        quiet: bool = False,
    ) -> None:
        self.state_name = state_name
        self.log_dir = log_dir
        self.quiet = quiet
        self._lock = threading.Lock()
        self._file: IO[str] | None = None

    def on_stdout(self, line: str) -> None:
        """Handle a stdout line from the provider.

        Prefixes the line with ``[state_name]`` and prints to stderr,
        then appends the raw line to the log file.
        When quiet=True, the print to stderr is suppressed.
        """
        if not self.quiet:
            print(f"[{self.state_name}] {line}", file=sys.stderr)
            sys.stderr.flush()
        self._write_to_file(line)

    def on_stderr(self, line: str) -> None:
        """Handle a stderr line from the provider.

        Prefixes the line with ``[state_name]`` and prints to stderr,
        then appends the raw line to the log file.
        When quiet=True, the print to stderr is suppressed.
        """
        if not self.quiet:
            print(f"[{self.state_name}] {line}", file=sys.stderr)
            sys.stderr.flush()
        self._write_to_file(line)

    def _write_to_file(self, line: str) -> None:
        """Write a line to the per-state log file, creating it lazily."""
        if self.log_dir is None:
            return
        with self._lock:
            if self._file is None:
                os.makedirs(str(self.log_dir), mode=0o700, exist_ok=True)
                log_path = self.log_dir / f"{self.state_name}{LOG_FILE_SUFFIX}"
                self._file = open(log_path, "a", encoding="utf-8")
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        """Flush and close the log file handle if open."""
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
