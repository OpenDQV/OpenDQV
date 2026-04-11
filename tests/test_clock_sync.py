"""Tests for core/clock_sync.py — NTP skew detection."""

import struct
import socket
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from opendqv.core.clock_sync import check_ntp_skew, _NTP_EPOCH_OFFSET


def _make_ntp_response(unix_timestamp: float) -> bytes:
    """Build a minimal 48-byte NTP response with the given transmit timestamp."""
    ntp_seconds = int(unix_timestamp) + _NTP_EPOCH_OFFSET
    ntp_fraction = int((unix_timestamp % 1) * 2**32)
    packet = bytearray(48)
    struct.pack_into("!I", packet, 40, ntp_seconds)
    struct.pack_into("!I", packet, 44, ntp_fraction)
    return bytes(packet)


class TestClockSyncResultStructure:
    def test_synced_result_has_all_keys(self):
        """A successful NTP response returns all expected keys."""
        now_unix = datetime.now(timezone.utc).timestamp()
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (_make_ntp_response(now_unix), ("1.2.3.4", 123))

        with patch("opendqv.core.clock_sync.socket.socket", return_value=mock_sock):
            result = check_ntp_skew()

        assert set(result.keys()) == {
            "status", "skew_ms", "ntp_source", "system_time", "ntp_time", "checked_at"
        }

    def test_unavailable_result_has_all_keys(self):
        """A network failure also returns all expected keys."""
        with patch("opendqv.core.clock_sync.socket.socket", side_effect=socket.timeout):
            result = check_ntp_skew()

        assert set(result.keys()) == {
            "status", "skew_ms", "ntp_source", "system_time", "ntp_time", "checked_at"
        }


class TestSkewThresholds:
    def _result_for_skew(self, skew_seconds: float) -> dict:
        now_unix = datetime.now(timezone.utc).timestamp()
        ntp_unix = now_unix - skew_seconds   # positive skew_seconds → system ahead
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (_make_ntp_response(ntp_unix), ("1.2.3.4", 123))
        with patch("opendqv.core.clock_sync.socket.socket", return_value=mock_sock):
            return check_ntp_skew()

    def test_small_skew_is_synced(self):
        """Skew well under threshold → synced. Uses 1s (not 4999ms) to avoid
        timing sensitivity on slow hardware where fixture setup adds latency."""
        result = self._result_for_skew(1.0)
        assert result["status"] == "synced"

    def test_large_skew_is_skewed(self):
        """Skew well over threshold → skewed. Uses 30s (not 5001ms) to avoid
        timing sensitivity on slow hardware."""
        result = self._result_for_skew(30.0)
        assert result["status"] == "skewed"

    def test_zero_skew_is_synced(self):
        """Zero skew → synced."""
        result = self._result_for_skew(0)
        assert result["status"] == "synced"


class TestNetworkFailure:
    def test_timeout_returns_unavailable(self):
        """socket.timeout → status unavailable, no exception raised."""
        with patch("opendqv.core.clock_sync.socket.socket", side_effect=socket.timeout):
            result = check_ntp_skew()
        assert result["status"] == "unavailable"
        assert result["ntp_time"] is None

    def test_os_error_returns_unavailable(self):
        """OSError (e.g. no route to host) → status unavailable, no exception raised."""
        with patch("opendqv.core.clock_sync.socket.socket", side_effect=OSError("no route")):
            result = check_ntp_skew()
        assert result["status"] == "unavailable"

    def test_short_response_returns_unavailable(self):
        """Response shorter than 48 bytes → status unavailable."""
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (b"\x00" * 10, ("1.2.3.4", 123))
        with patch("opendqv.core.clock_sync.socket.socket", return_value=mock_sock):
            result = check_ntp_skew()
        assert result["status"] == "unavailable"


class TestTimestampFormat:
    def test_ntp_time_is_utc_iso_string(self):
        """ntp_time is a parseable ISO 8601 UTC string."""
        now_unix = datetime.now(timezone.utc).timestamp()
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (_make_ntp_response(now_unix), ("1.2.3.4", 123))

        with patch("opendqv.core.clock_sync.socket.socket", return_value=mock_sock):
            result = check_ntp_skew()

        dt = datetime.fromisoformat(result["ntp_time"].replace("Z", "+00:00"))
        assert dt.tzinfo is not None

    def test_system_time_is_utc_iso_string(self):
        """system_time is a parseable ISO 8601 UTC string."""
        now_unix = datetime.now(timezone.utc).timestamp()
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (_make_ntp_response(now_unix), ("1.2.3.4", 123))

        with patch("opendqv.core.clock_sync.socket.socket", return_value=mock_sock):
            result = check_ntp_skew()

        dt = datetime.fromisoformat(result["system_time"].replace("Z", "+00:00"))
        assert dt.tzinfo is not None
