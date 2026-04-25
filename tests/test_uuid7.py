"""Tests for the RFC 9562 §5.7 UUID v7 shim (opendqv.core._uuid7)."""

import time
import uuid

from opendqv.core._uuid7 import uuid7


def test_returns_uuid_object():
    assert isinstance(uuid7(), uuid.UUID)


def test_version_bits_are_seven():
    for _ in range(100):
        assert uuid7().version == 7


def test_variant_bits_are_rfc_4122():
    for _ in range(100):
        u = uuid7()
        assert (u.int >> 62) & 0x3 == 0b10


def test_timestamp_matches_now_within_a_second():
    before_ms = int(time.time() * 1000)
    u = uuid7()
    after_ms = int(time.time() * 1000) + 1
    ts_ms = u.int >> 80
    assert before_ms - 5 <= ts_ms <= after_ms + 5


def test_lex_sort_matches_generation_order_across_ms():
    earlier = uuid7()
    time.sleep(0.005)
    later = uuid7()
    assert str(earlier) < str(later)
    assert earlier.int < later.int


def test_uniqueness_under_burst():
    ids = {uuid7() for _ in range(10_000)}
    assert len(ids) == 10_000


def test_string_form_is_canonical():
    s = str(uuid7())
    assert len(s) == 36
    assert s[8] == s[13] == s[18] == s[23] == "-"
    assert s[14] == "7"
