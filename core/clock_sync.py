"""
Clock synchronisation check for audit log integrity.

At startup, OpenDQV queries an NTP server and records the result in the node
health log. This gives auditors evidence about whether the system clock was
accurate when audit timestamps were written.

The check is best-effort: if the network is unavailable the service still starts
and the result is recorded as 'unavailable'. It never raises.

FUTURE (commercial/enterprise): Replace socket NTP check with RFC 3161 trusted
timestamp anchoring — cryptographic proof of timestamp accuracy from a trusted
timestamp authority (TSA). This is the correct upgrade path for regulated
environments (FCA, SOX, GDPR audit obligations).
"""

import socket
import struct
from datetime import datetime, timezone

_NTP_SOURCE = "pool.ntp.org"
_NTP_PORT = 123
_NTP_TIMEOUT = 2.0
_NTP_EPOCH_OFFSET = 2208988800  # seconds between 1900-01-01 and 1970-01-01
_SKEW_THRESHOLD_MS = 5000       # 5 seconds — anything above is flagged as skewed


def check_ntp_skew(
    ntp_source: str = _NTP_SOURCE,
    timeout: float = _NTP_TIMEOUT,
) -> dict:
    """
    Query an NTP server and return clock skew information.

    Returns a dict with keys:
        status      "synced" | "skewed" | "unavailable"
        skew_ms     int — positive means system clock is ahead of NTP
        ntp_source  str
        system_time ISO 8601 UTC string at check time
        ntp_time    ISO 8601 UTC string from NTP (or None if unavailable)
        checked_at  ISO 8601 UTC string
    """
    checked_at = datetime.now(timezone.utc)
    system_time_str = checked_at.isoformat()

    try:
        # Standard NTP client request: 48-byte packet, LI=0, VN=3, Mode=3
        packet = b"\x1b" + b"\x00" * 47

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (ntp_source, _NTP_PORT))
            data, _ = sock.recvfrom(1024)
        finally:
            sock.close()

        if len(data) < 48:
            raise ValueError(f"NTP response too short: {len(data)} bytes")

        # Transmit timestamp: bytes 40–47, seconds since 1900-01-01 (big-endian)
        ntp_seconds = struct.unpack("!I", data[40:44])[0]
        ntp_fraction = struct.unpack("!I", data[44:48])[0]
        ntp_unix = ntp_seconds - _NTP_EPOCH_OFFSET + ntp_fraction / 2**32

        ntp_dt = datetime.fromtimestamp(ntp_unix, tz=timezone.utc)
        ntp_time_str = ntp_dt.isoformat()

        skew_ms = int((checked_at.timestamp() - ntp_unix) * 1000)
        status = "synced" if abs(skew_ms) <= _SKEW_THRESHOLD_MS else "skewed"

        return {
            "status": status,
            "skew_ms": skew_ms,
            "ntp_source": ntp_source,
            "system_time": system_time_str,
            "ntp_time": ntp_time_str,
            "checked_at": system_time_str,
        }

    except Exception:
        return {
            "status": "unavailable",
            "skew_ms": 0,
            "ntp_source": ntp_source,
            "system_time": system_time_str,
            "ntp_time": None,
            "checked_at": system_time_str,
        }
