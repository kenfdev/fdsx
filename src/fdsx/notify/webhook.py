from typing import Any
from urllib.parse import urlparse

import structlog
from httpx import Client, HTTPError, TimeoutException

from fdsx.core.variables import resolve_template
from fdsx.models.flow import NotifyConfig

logger = structlog.get_logger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10


def _redact_url(url: str) -> str:
    """Return only scheme and host from a URL; hide path/query which may contain secrets."""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/***"
    except Exception:
        return "[url-redacted]"


def send_webhook(url: str, message: str) -> bool:
    """Send a webhook notification.

    Args:
        url: The webhook URL to POST to
        message: The message body to send as JSON {"text": message}

    Returns:
        True on success (2xx status), False on any failure
    """
    payload = {"text": message}

    try:
        with Client(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
            response = client.post(url, json=payload)
            if response.status_code >= 200 and response.status_code < 300:
                return True
            logger.warning(
                "webhook_non_2xx_response",
                url=_redact_url(url),
                status_code=response.status_code,
            )
            return False
    except TimeoutException:
        logger.warning(
            "webhook_timeout", url=_redact_url(url), timeout=WEBHOOK_TIMEOUT_SECONDS
        )
        return False
    except HTTPError as e:
        logger.warning(
            "webhook_http_error", url=_redact_url(url), error=type(e).__name__
        )
        return False
    except Exception as e:
        logger.warning(
            "webhook_unknown_error", url=_redact_url(url), error=type(e).__name__
        )
        return False


def send_notification(notify: NotifyConfig, state_dict: dict[str, Any]) -> None:
    """Send a webhook notification with resolved template variables.

    Args:
        notify: The notification configuration containing webhook URL and template
        state_dict: The current state dictionary for template variable resolution
    """
    resolved_message = resolve_template(notify.webhook.template, state_dict)
    send_webhook(notify.webhook.url, resolved_message)
