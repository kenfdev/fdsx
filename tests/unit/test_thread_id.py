"""Tests for thread ID generation (YYYY-MM-DD-HHmmss-<hex6> format)."""

import re
from datetime import datetime

from fdsx.core.thread_id import generate_thread_id
from fdsx.logging.recorder import THREAD_ID_PATTERN


class TestThreadIdFormat:
    """FR-1.1: Generated IDs match YYYY-MM-DD-HHmmss-[a-f0-9]{6} format."""

    def test_thread_id_matches_format(self) -> None:
        """Generated ID must match YYYY-MM-DD-HHmmss-<hex6> format."""
        thread_id = generate_thread_id()
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[a-f0-9]{6}$")
        assert pattern.match(thread_id), f"Invalid thread ID format: {thread_id}"

    def test_thread_id_timestamp_portion_matches_local_time(self) -> None:
        """Timestamp portion (YYYY-MM-DD-HHMMSS) must match local time."""
        thread_id = generate_thread_id()
        timestamp_str = thread_id[:17]
        expected = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        assert timestamp_str == expected, (
            f"Timestamp mismatch: expected {expected}, got {timestamp_str}"
        )


class TestThreadIdUniqueness:
    """FR-1.2: Each generated ID is unique."""

    def test_100_consecutive_calls_are_unique(self) -> None:
        """100 consecutive generate_thread_id() calls must all be distinct."""
        ids = {generate_thread_id() for _ in range(100)}
        assert len(ids) == 100, "Expected 100 unique thread IDs"


class TestThreadIdPatternAcceptance:
    """FR-1.3: Generated IDs pass THREAD_ID_PATTERN validation."""

    def test_thread_id_passes_thread_id_pattern(self) -> None:
        """Generated thread ID must pass THREAD_ID_PATTERN validation."""
        thread_id = generate_thread_id()
        assert THREAD_ID_PATTERN.match(thread_id), (
            f"Thread ID '{thread_id}' failed THREAD_ID_PATTERN validation"
        )

    def test_multiple_thread_ids_pass_thread_id_pattern(self) -> None:
        """Multiple generated IDs all pass THREAD_ID_PATTERN."""
        for _ in range(20):
            thread_id = generate_thread_id()
            assert THREAD_ID_PATTERN.match(thread_id), (
                f"Thread ID '{thread_id}' failed THREAD_ID_PATTERN validation"
            )
