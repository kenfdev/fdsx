from unittest.mock import MagicMock, patch

from fdsx.providers.base import ProviderResult


def _patch_sleep(execution_mod, sleep_times):
    """Patch time.sleep in the execution module.

    time.sleep is only called from execution.py inside execute_with_retry.
    """
    orig_execution_sleep = execution_mod.time.sleep
    recorder = lambda s: sleep_times.append(s)
    execution_mod.time.sleep = recorder
    return orig_execution_sleep


def _restore_sleep(execution_mod, originals):
    execution_mod.time.sleep = originals


class TestExponentialBackoff:
    """T064: Unit tests for exponential backoff in retry loops."""

    def test_backoff_delays_for_retries(self):
        """Verify backoff delays (1, 2, 4 seconds) for 3 retries."""
        import fdsx.core.compiler as compiler
        import fdsx.core.compiler.execution as execution
        from fdsx.models.flow import Flow

        state_dict = {}
        state = MagicMock()
        state.provider = "openai"
        state.model = "gpt-4"
        state.prompt_template = "test"
        state.command = None
        state.timeout_seconds = 30
        state.retry = 3
        state.extract = None
        state.result_path = "result"
        state.max_iterations = None

        flow = MagicMock(spec=Flow)
        flow.providers = None

        call_count = 0
        sleep_times = []

        def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return ProviderResult(exit_code=1, stdout="", stderr="error")
            return ProviderResult(exit_code=0, stdout="success", stderr="")

        with patch("fdsx.core.compiler.nodes.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.execute = mock_execute
            mock_get_provider.return_value = mock_provider

            originals = _patch_sleep(execution, sleep_times)

            try:
                compiler._create_task_node("test_state", state, flow, None)(state_dict)
            except RuntimeError:
                pass
            finally:
                _restore_sleep(execution, originals)

            assert len(sleep_times) == 3
            assert sleep_times == [1, 2, 4]

    def test_first_attempt_has_no_delay(self):
        """Verify first attempt has no delay."""
        import fdsx.core.compiler as compiler
        import fdsx.core.compiler.execution as execution
        from fdsx.models.flow import Flow

        state_dict = {}
        state = MagicMock()
        state.provider = "openai"
        state.model = "gpt-4"
        state.prompt_template = "test"
        state.command = None
        state.timeout_seconds = 30
        state.retry = 1
        state.extract = None
        state.result_path = "result"
        state.max_iterations = None

        flow = MagicMock(spec=Flow)
        flow.providers = None

        call_count = 0
        sleep_times = []

        def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return ProviderResult(exit_code=1, stdout="", stderr="error")
            return ProviderResult(exit_code=0, stdout="success", stderr="")

        with patch("fdsx.core.compiler.nodes.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.execute = mock_execute
            mock_get_provider.return_value = mock_provider

            originals = _patch_sleep(execution, sleep_times)

            try:
                compiler._create_task_node("test_state", state, flow, None)(state_dict)
            except RuntimeError:
                pass
            finally:
                _restore_sleep(execution, originals)

            assert len(sleep_times) == 1

    def test_exception_triggers_retry_with_backoff(self):
        """Verify timeout exception triggers retry with backoff."""
        import fdsx.core.compiler as compiler
        import fdsx.core.compiler.execution as execution
        from fdsx.models.flow import Flow

        state_dict = {}
        state = MagicMock()
        state.provider = "openai"
        state.model = "gpt-4"
        state.prompt_template = "test"
        state.command = None
        state.timeout_seconds = 30
        state.retry = 2
        state.extract = None
        state.result_path = "result"
        state.max_iterations = None

        flow = MagicMock(spec=Flow)
        flow.providers = None

        call_count = 0
        sleep_times = []

        def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise TimeoutError("Command timed out")
            return ProviderResult(exit_code=0, stdout="success", stderr="")

        with patch("fdsx.core.compiler.nodes.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.execute = mock_execute
            mock_get_provider.return_value = mock_provider

            originals = _patch_sleep(execution, sleep_times)

            try:
                compiler._create_task_node("test_state", state, flow, None)(state_dict)
            except RuntimeError:
                pass
            finally:
                _restore_sleep(execution, originals)

            assert len(sleep_times) == 2
            assert sleep_times == [1, 2]

    def test_backoff_capped_at_30_seconds(self):
        """Verify cap at 30s for high retry counts."""
        import fdsx.core.compiler as compiler
        import fdsx.core.compiler.execution as execution
        from fdsx.models.flow import Flow

        state_dict = {}
        state = MagicMock()
        state.provider = "openai"
        state.model = "gpt-4"
        state.prompt_template = "test"
        state.command = None
        state.timeout_seconds = 30
        state.retry = 10
        state.extract = None
        state.result_path = "result"
        state.max_iterations = None

        flow = MagicMock(spec=Flow)
        flow.providers = None

        call_count = 0
        sleep_times = []

        def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ProviderResult(exit_code=1, stdout="", stderr="error")

        with patch("fdsx.core.compiler.nodes.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.execute = mock_execute
            mock_get_provider.return_value = mock_provider

            originals = _patch_sleep(execution, sleep_times)

            try:
                compiler._create_task_node("test_state", state, flow, None)(state_dict)
            except RuntimeError:
                pass
            finally:
                _restore_sleep(execution, originals)

            assert len(sleep_times) == 10
            for i in range(1, 10):
                expected = min(2 ** (i - 1), 30)
                assert sleep_times[i - 1] == expected

    def test_branch_executor_backoff(self):
        """Verify branch executor also uses exponential backoff."""
        import fdsx.core.compiler as compiler
        import fdsx.core.compiler.execution as execution
        from fdsx.models.flow import Flow

        state_dict = {"_branch_index": 0}
        branch = MagicMock()
        branch.provider = "openai"
        branch.model = "gpt-4"
        branch.prompt_template = "test"
        branch.timeout_seconds = 30
        branch.retry = 2
        branch.extract = None
        branch.command = None

        parallel_state = MagicMock()
        parallel_state.branches = [branch]

        flow = MagicMock(spec=Flow)
        flow.providers = None

        call_count = 0
        sleep_times = []

        def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return ProviderResult(exit_code=1, stdout="", stderr="error")
            return ProviderResult(exit_code=0, stdout="success", stderr="")

        with patch("fdsx.core.compiler.parallel.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.execute = mock_execute
            mock_get_provider.return_value = mock_provider

            originals = _patch_sleep(execution, sleep_times)

            try:
                compiler._create_branch_executor(
                    "test_state", parallel_state, flow, None
                )(state_dict)
            except (RuntimeError, KeyError, TypeError):
                pass
            finally:
                _restore_sleep(execution, originals)

            assert len(sleep_times) == 2
            assert sleep_times[0] == 1
            assert sleep_times[1] == 2
