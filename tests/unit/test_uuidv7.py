"""Tests for UUIDv7 run ID generation (Phase 1: FR-1.1–FR-1.6)."""

import re
import time

import uuid_utils

from fdsx.logging.recorder import THREAD_ID_PATTERN


class TestUUIDv7Format:
    """FR-1.1: Generated IDs are valid UUID format."""

    def test_uuid7_is_valid_uuid_format(self) -> None:
        """Generated ID must match standard UUID string format."""
        uid = str(uuid_utils.uuid7())
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )
        assert uuid_pattern.match(uid), f"Not a valid UUID format: {uid}"

    def test_uuid7_version_nibble_is_7(self) -> None:
        """FR-1.2: Version nibble (13th hex char) must be '7'."""
        uid = str(uuid_utils.uuid7())
        # UUID format: xxxxxxxx-xxxx-Mxxx-Nxxx-xxxxxxxxxxxx
        # M (version) is at index 14 (after removing dashes: position 12)
        # With dashes: positions 0-7, dash, 9-12, dash, 14-17, dash, 19-22, dash, 24-35
        version_nibble = uid[14]
        assert version_nibble == "7", (
            f"Expected version nibble '7', got '{version_nibble}' in {uid}"
        )

    def test_uuid7_variant_bits(self) -> None:
        """FR-1.3: Variant bits (first nibble at position 19) must be '8', '9', 'a', or 'b'."""
        uid = str(uuid_utils.uuid7())
        variant_nibble = uid[19]
        assert variant_nibble in ("8", "9", "a", "b"), (
            f"Expected RFC 4122 variant nibble, got '{variant_nibble}' in {uid}"
        )


class TestUUIDv7ChronologicalSort:
    """FR-1.4: UUIDv7 IDs sort chronologically (time-sortable property)."""

    def test_sequential_uuids_sort_chronologically(self) -> None:
        """UUIDv7 IDs generated sequentially must sort in generation order."""
        ids = []
        for _ in range(10):
            ids.append(str(uuid_utils.uuid7()))
            # Small sleep to ensure distinct millisecond timestamps
            time.sleep(0.002)

        sorted_ids = sorted(ids)
        assert ids == sorted_ids, (
            f"UUIDv7 IDs are not in chronological order.\n"
            f"Generated: {ids}\n"
            f"Sorted:    {sorted_ids}"
        )

    def test_two_uuids_in_order(self) -> None:
        """Two UUIDv7s generated sequentially: first < second."""
        uid1 = str(uuid_utils.uuid7())
        time.sleep(0.002)
        uid2 = str(uuid_utils.uuid7())
        assert uid1 < uid2, f"Expected {uid1} < {uid2}"


class TestUUIDv7ThreadIdPatternAcceptance:
    """FR-1.5: Generated UUIDv7 IDs pass THREAD_ID_PATTERN validation."""

    def test_uuid7_passes_thread_id_pattern(self) -> None:
        """UUIDv7 string representation must pass existing THREAD_ID_PATTERN."""
        uid = str(uuid_utils.uuid7())
        # THREAD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
        # UUIDs use hex chars [0-9a-f] and dashes '-' — both allowed
        assert THREAD_ID_PATTERN.match(uid), (
            f"UUIDv7 '{uid}' failed THREAD_ID_PATTERN validation"
        )

    def test_multiple_uuid7s_pass_thread_id_pattern(self) -> None:
        """Multiple UUIDv7s all pass THREAD_ID_PATTERN."""
        for _ in range(20):
            uid = str(uuid_utils.uuid7())
            assert THREAD_ID_PATTERN.match(uid), (
                f"UUIDv7 '{uid}' failed THREAD_ID_PATTERN validation"
            )

    def test_uuid7_no_uppercase_hex(self) -> None:
        """UUIDv7 string contains only lowercase hex digits and dashes."""
        uid = str(uuid_utils.uuid7())
        assert uid == uid.lower(), f"Expected lowercase UUID, got: {uid}"


class TestUUIDv7Uniqueness:
    """FR-1.6: Each generated ID is unique."""

    def test_uuid7_generates_unique_ids(self) -> None:
        """100 generated UUIDv7s must all be distinct."""
        ids = {str(uuid_utils.uuid7()) for _ in range(100)}
        assert len(ids) == 100, "Expected 100 unique UUIDv7 IDs"
