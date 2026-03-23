"""Unit tests for StreamLogger iteration-numbered log files (T005).

Tests verify:
- StreamLogger with iteration=1 creates {state}_1.log (not {state}.log)
- StreamLogger with iteration=2 creates {state}_2.log as a separate file
- StreamLogger with branch-prefixed state_name produces correctly named log files
- Retry output within the same iteration appends to the same file
- Iteration numbering is 1-based
"""

from fdsx.logging.stream_logger import LOG_FILE_SUFFIX, StreamLogger


class TestStreamLoggerIterationNaming:
    """Tests for iteration-numbered log file naming."""

    def test_default_iteration_creates_state_1_log(self, tmp_path):
        """StreamLogger without explicit iteration creates {state}_1.log."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("plan", log_dir)
        logger.on_stdout("output line")
        logger.close()

        assert (log_dir / f"plan_1{LOG_FILE_SUFFIX}").exists()
        assert not (log_dir / f"plan{LOG_FILE_SUFFIX}").exists()

    def test_iteration_2_creates_state_2_log(self, tmp_path):
        """StreamLogger with iteration=2 creates {state}_2.log."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("plan", log_dir, iteration=2)
        logger.on_stdout("second iteration output")
        logger.close()

        assert (log_dir / f"plan_2{LOG_FILE_SUFFIX}").exists()
        assert not (log_dir / f"plan_1{LOG_FILE_SUFFIX}").exists()

    def test_separate_iterations_are_separate_files(self, tmp_path):
        """Two instances with iteration=1 and iteration=2 write to different files."""
        log_dir = tmp_path / "logs"

        logger1 = StreamLogger("implement", log_dir, iteration=1)
        logger1.on_stdout("first iteration content")
        logger1.close()

        logger2 = StreamLogger("implement", log_dir, iteration=2)
        logger2.on_stdout("second iteration content")
        logger2.close()

        path1 = log_dir / f"implement_1{LOG_FILE_SUFFIX}"
        path2 = log_dir / f"implement_2{LOG_FILE_SUFFIX}"

        assert path1.exists()
        assert path2.exists()

        content1 = path1.read_text(encoding="utf-8")
        content2 = path2.read_text(encoding="utf-8")

        assert "first iteration content" in content1
        assert "second iteration content" not in content1
        assert "second iteration content" in content2
        assert "first iteration content" not in content2

    def test_parallel_branch_naming(self, tmp_path):
        """StreamLogger with state_name='review_branch1', iteration=1 creates review_branch1_1.log."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("review_branch1", log_dir, iteration=1)
        logger.on_stdout("branch output")
        logger.close()

        assert (log_dir / f"review_branch1_1{LOG_FILE_SUFFIX}").exists()

    def test_retry_appends_to_same_iteration_file(self, tmp_path):
        """Multiple writes to same StreamLogger instance all go to single {state}_{iteration}.log."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("plan", log_dir, iteration=3)

        logger.on_stdout("attempt 1 output")
        logger.on_stdout("retry attempt output")
        logger.close()

        log_path = log_dir / f"plan_3{LOG_FILE_SUFFIX}"
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "attempt 1 output" in content
        assert "retry attempt output" in content

    def test_first_iteration_starts_at_1(self, tmp_path):
        """Default iteration is 1 (1-based numbering)."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("review", log_dir)
        assert logger.iteration == 1

        logger.on_stdout("first run")
        logger.close()

        # Must produce _1.log, not _0.log
        assert (log_dir / f"review_1{LOG_FILE_SUFFIX}").exists()
        assert not (log_dir / f"review_0{LOG_FILE_SUFFIX}").exists()

    def test_iteration_stored_on_instance(self, tmp_path):
        """iteration parameter is stored as self.iteration."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("plan", log_dir, iteration=5)
        assert logger.iteration == 5

    def test_no_log_file_when_no_output_iteration(self, tmp_path):
        """No log file created when no output is produced, even with iteration set."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("plan", log_dir, iteration=2)
        logger.close()

        assert not (log_dir / f"plan_2{LOG_FILE_SUFFIX}").exists()

    def test_state_name_with_underscores(self, tmp_path):
        """State names containing underscores produce unambiguous filenames."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("my_state", log_dir, iteration=1)
        logger.on_stdout("output")
        logger.close()

        # my_state with iteration=1 → my_state_1.log
        assert (log_dir / f"my_state_1{LOG_FILE_SUFFIX}").exists()

    def test_quiet_mode_still_uses_iteration_filename(self, tmp_path):
        """quiet=True still writes to the iteration-numbered log file."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("plan", log_dir, quiet=True, iteration=2)
        logger.on_stdout("quiet output")
        logger.close()

        assert (log_dir / f"plan_2{LOG_FILE_SUFFIX}").exists()
        content = (log_dir / f"plan_2{LOG_FILE_SUFFIX}").read_text(encoding="utf-8")
        assert "quiet output" in content

    def test_underscore_in_state_name_no_ambiguity(self, tmp_path):
        """State 'my_state' (iter=1) and state 'my_state_1' (iter=1) produce distinct files.

        Ensures no filename collision between a state named 'my_state_1' (iteration 1)
        and a state named 'my_state' (iteration 1). Both should coexist unambiguously
        in the same log_dir.
        """
        log_dir = tmp_path / "logs"

        # State "my_state" at iteration 1 → my_state_1.log
        logger_a = StreamLogger("my_state", log_dir, iteration=1)
        logger_a.on_stdout("alpha payload")
        logger_a.close()

        # State "my_state_1" at iteration 1 → my_state_1_1.log
        logger_b = StreamLogger("my_state_1", log_dir, iteration=1)
        logger_b.on_stdout("beta payload")
        logger_b.close()

        path_a = log_dir / f"my_state_1{LOG_FILE_SUFFIX}"
        path_b = log_dir / f"my_state_1_1{LOG_FILE_SUFFIX}"

        # Both files must exist and be distinct
        assert path_a.exists(), f"Expected my_state_1.log at {path_a}"
        assert path_b.exists(), f"Expected my_state_1_1.log at {path_b}"

        content_a = path_a.read_text(encoding="utf-8")
        content_b = path_b.read_text(encoding="utf-8")

        assert "alpha payload" in content_a
        assert "beta payload" not in content_a
        assert "beta payload" in content_b
        assert "alpha payload" not in content_b
