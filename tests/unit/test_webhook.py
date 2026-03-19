from unittest.mock import MagicMock, patch

from fdsx.notify.webhook import send_notification, send_webhook
from fdsx.models.flow import NotifyConfig, WebhookConfig


class TestSendWebhook:
    def test_successful_post_returns_true(self):
        with patch("fdsx.notify.webhook.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_client.return_value
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response

            result = send_webhook("https://example.com/webhook", "test message")

            assert result is True
            mock_client.return_value.post.assert_called_once_with(
                "https://example.com/webhook", json={"text": "test message"}
            )

    def test_network_error_returns_false(self):
        from httpx import HTTPError

        with patch("fdsx.notify.webhook.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_client.return_value
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.side_effect = HTTPError("Connection failed")

            result = send_webhook("https://example.com/webhook", "test message")

            assert result is False

    def test_non_2xx_status_returns_false(self):
        with patch("fdsx.notify.webhook.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_client.return_value
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response

            result = send_webhook("https://example.com/webhook", "test message")

            assert result is False

    def test_timeout_returns_false(self):
        from httpx import TimeoutException

        with patch("fdsx.notify.webhook.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_client.return_value
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.side_effect = TimeoutException(
                "Request timeout"
            )

            result = send_webhook("https://example.com/webhook", "test message")

            assert result is False

    def test_timeout_logs_webhook_timeout_event_not_http_error(self):
        """Regression: TimeoutException must use 'webhook_timeout' log event, not 'webhook_http_error'.

        Before the fix, TimeoutException (a subclass of HTTPError) was caught by
        the broader `except HTTPError` handler, silently logging the wrong event.
        """
        import structlog.testing
        from httpx import TimeoutException

        with patch("fdsx.notify.webhook.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_client.return_value
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.side_effect = TimeoutException(
                "Request timeout"
            )

            with structlog.testing.capture_logs() as log_output:
                result = send_webhook("https://example.com/webhook", "test message")

        assert result is False
        assert len(log_output) == 1
        assert log_output[0]["event"] == "webhook_timeout"
        assert "timeout" in log_output[0]

    def test_logs_do_not_expose_raw_url_or_message_body(self):
        """Regression: log events must not contain raw webhook URL or message body.

        Webhook URLs often contain bearer tokens in the path/query.  The resolved
        message is derived from workflow state and may contain sensitive content.
        Both must be redacted/omitted from all log events.
        """
        import structlog.testing
        from httpx import TimeoutException

        secret_url = "https://hooks.example.com/services/TOKEN123/SECRET456"
        sensitive_message = "Confidential plan: deploy API key abc123"

        with patch("fdsx.notify.webhook.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_client.return_value
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.side_effect = TimeoutException("timeout")

            with structlog.testing.capture_logs() as log_output:
                send_webhook(secret_url, sensitive_message)

        assert len(log_output) == 1
        log_entry = log_output[0]
        # Raw URL must not appear in any log field
        for value in log_entry.values():
            assert secret_url not in str(value), (
                f"Raw URL leaked in log field: {value!r}"
            )
        # Message body must not appear in any log field
        for value in log_entry.values():
            assert sensitive_message not in str(value), (
                f"Message body leaked in log field: {value!r}"
            )
        # Redacted URL (scheme+host only) must be present
        assert "hooks.example.com" in str(log_entry.get("url", ""))
        assert "TOKEN123" not in str(log_entry.get("url", ""))

    def test_non_2xx_log_does_not_expose_url_or_message(self):
        """Regression: non-2xx warning must redact URL and omit message body."""
        import structlog.testing

        secret_url = "https://hooks.example.com/services/SECRETTOKEN"

        with patch("fdsx.notify.webhook.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_client.return_value
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.return_value = mock_response

            with structlog.testing.capture_logs() as log_output:
                send_webhook(secret_url, "sensitive data")

        assert len(log_output) == 1
        log_entry = log_output[0]
        assert "SECRETTOKEN" not in str(log_entry)
        assert "sensitive data" not in str(log_entry)
        assert log_entry["event"] == "webhook_non_2xx_response"
        assert log_entry["status_code"] == 403


class TestRedactUrl:
    """Unit tests for the _redact_url helper."""

    def test_hides_path_and_query(self):
        from fdsx.notify.webhook import _redact_url

        result = _redact_url("https://hooks.example.com/services/TOKEN/SECRET?foo=bar")
        assert result == "https://hooks.example.com/***"
        assert "TOKEN" not in result
        assert "SECRET" not in result
        assert "foo" not in result

    def test_preserves_scheme_and_host(self):
        from fdsx.notify.webhook import _redact_url

        result = _redact_url("https://example.com/webhook")
        assert result.startswith("https://example.com/")

    def test_handles_malformed_url_gracefully(self):
        from fdsx.notify.webhook import _redact_url

        result = _redact_url("not-a-url")
        assert "not-a-url" not in result or result == "not-a-url/***"
        # Must not raise


class TestSendNotification:
    def test_template_variables_are_resolved_before_sending(self):
        with patch("fdsx.notify.webhook.send_webhook") as mock_send:
            mock_send.return_value = True

            notify = NotifyConfig(
                webhook=WebhookConfig(
                    url="https://example.com/webhook",
                    template="Approval needed for {task_name} by {assignee}",
                )
            )
            state_dict = {
                "task_name": "Fix bug #123",
                "assignee": "alice",
            }

            send_notification(notify, state_dict)

            mock_send.assert_called_once_with(
                "https://example.com/webhook",
                "Approval needed for Fix bug #123 by alice",
            )

    def test_webhook_failure_does_not_raise_exception(self):
        with patch("fdsx.notify.webhook.send_webhook") as mock_send:
            mock_send.return_value = False

            notify = NotifyConfig(
                webhook=WebhookConfig(
                    url="https://example.com/webhook",
                    template="Test message",
                )
            )
            state_dict = {}

            result = send_notification(notify, state_dict)

            assert result is None
            mock_send.assert_called_once()

    def test_http_error_message_not_in_logs(self):
        """Regression: HTTPError str() must not appear in logs - may contain secrets.

        HTTPError exceptions often embed the full request URL which may contain
        bearer tokens or other secrets.
        """
        import structlog.testing
        from httpx import HTTPError

        secret_url = "https://hooks.example.com/services/TOKEN123/SECRET"

        with patch("fdsx.notify.webhook.Client") as mock_client:
            mock_client.return_value.__enter__ = MagicMock(
                return_value=mock_client.return_value
            )
            mock_client.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.return_value.post.side_effect = HTTPError(
                f"GET {secret_url} failed"
            )

            with structlog.testing.capture_logs() as log_output:
                send_webhook(secret_url, "test message")

        assert len(log_output) == 1
        log_entry = log_output[0]
        # The error field must contain only the exception type name, not str(e)
        assert log_entry["error"] == "HTTPError"
        # The secret URL must not appear in any log field
        for value in log_entry.values():
            assert "TOKEN123" not in str(value), f"Secret leaked in log: {value!r}"
