from unittest.mock import patch

from fdsx.core.engine import FlowResult, run_flow
from fdsx.core.loader import load_flow
from tests import FIXTURES_DIR


class TestWaitFlow:
    def test_wait_state_loads_correctly(self):
        """Test that wait state flow validates correctly."""
        path = FIXTURES_DIR / "wait_approval.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

    def test_wait_state_prompt_with_approve_selection(self, tmp_path):
        """Test Wait → Choice routing: select approve → verify flow takes approve branch."""
        path = FIXTURES_DIR / "wait_approval.yaml"

        # Mock stdin to provide "1" (approve)
        with patch("builtins.input", return_value="1"):
            result = run_flow(path, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert "plan_output" in result.results
        assert "approval_decision" in result.results
        assert result.results["approval_decision"] == "approve"
        assert "implementation_output" in result.results

    def test_wait_state_prompt_with_reject_selection(self, tmp_path):
        """Test Wait → Choice routing: select reject → verify flow takes reject branch."""
        path = FIXTURES_DIR / "wait_approval.yaml"

        # Patch is_interactive to force the interactive prompt path in CI
        with (
            patch("fdsx.core.mode.is_interactive", return_value=True),
            patch("builtins.input", return_value="2"),
        ):
            result = run_flow(path, base_dir=tmp_path)

        assert "plan_output" in result.results
        assert "approval_decision" in result.results
        assert result.results["approval_decision"] == "reject"
        assert "rejected_output" in result.results

    def test_wait_state_prompt_with_invalid_input_then_valid(self, tmp_path):
        """Test Wait state re-prompt on invalid input."""
        path = FIXTURES_DIR / "wait_approval.yaml"

        # Mock stdin to provide invalid input first, then "1" (approve)
        with patch("builtins.input", side_effect=["invalid", "5", "1"]):
            result = run_flow(path, base_dir=tmp_path)

        assert "approval_decision" in result.results
        assert result.results["approval_decision"] == "approve"

    def test_wait_webhook_notification_sent(self, tmp_path):
        """Test webhook notification: verify POST is sent when notify is configured."""
        path = FIXTURES_DIR / "wait_webhook.yaml"

        with patch("fdsx.notify.webhook.send_webhook") as mock_webhook:
            mock_webhook.return_value = True

            with patch("builtins.input", return_value="1"):
                result = run_flow(path, base_dir=tmp_path)

            # Verify webhook was called
            assert mock_webhook.called
            call_args = mock_webhook.call_args
            assert call_args[0][0] == "https://example.com/webhook"
            assert "Approval needed" in call_args[0][1]
            assert "plan_output" in result.results
            assert result.results["approval_decision"] == "approve"

    def test_wait_webhook_sent_exactly_once_on_approve(self, tmp_path):
        """Regression: webhook must fire exactly once, not on every resume cycle.

        Before the fix, send_notification() was called before interrupt() in the same
        node function.  LangGraph re-executes the entire node on resume, so the webhook
        fired twice per approval cycle (call_count=2 instead of 1).
        Fix: split into notify-pre node (checkpointed) + interrupt node.
        """
        path = FIXTURES_DIR / "wait_webhook.yaml"

        with patch("fdsx.notify.webhook.send_webhook") as mock_webhook:
            mock_webhook.return_value = True

            with patch("builtins.input", return_value="1"):
                result = run_flow(path, base_dir=tmp_path)

        assert result.results["approval_decision"] == "approve"
        assert mock_webhook.call_count == 1, (
            f"Expected webhook called exactly once, got {mock_webhook.call_count}"
        )

    def test_wait_webhook_failure_does_not_block_flow(self, tmp_path):
        """Test webhook failure: verify flow continues normally (warning logged, prompt still shown)."""
        path = FIXTURES_DIR / "wait_webhook.yaml"

        with patch("fdsx.notify.webhook.send_webhook") as mock_webhook:
            # Webhook fails but flow should continue
            mock_webhook.return_value = False

            with patch("builtins.input", return_value="1"):
                result = run_flow(path, base_dir=tmp_path)

            # Verify webhook was called
            assert mock_webhook.called
            # Flow should still complete successfully
            assert result.results["approval_decision"] == "approve"
            assert "implementation_output" in result.results
