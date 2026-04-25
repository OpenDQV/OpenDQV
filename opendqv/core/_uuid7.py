"""UUID v7 shim — RFC 9562 §5.7.

stdlib `uuid.uuid7()` exists from Python 3.14 (October 2025). OpenDQV supports
Python 3.11+, so we bring our own. Layout:

    48 bits  unix timestamp (ms, big-endian)
     4 bits  version (0b0111)
    12 bits  rand_a
     2 bits  variant (0b10)
    62 bits  rand_b
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate an RFC 9562 §5.7 UUID v7 (time-ordered, lex-sortable)."""
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand = int.from_bytes(os.urandom(10), "big")
    rand_a = (rand >> 64) & 0x0FFF
    rand_b = rand & 0x3FFFFFFFFFFFFFFF
    value = (
        (ts_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0x2 << 62)
        | rand_b
    )
    return uuid.UUID(int=value)
