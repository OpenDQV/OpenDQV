"""
TRACE_LOG — per-record tamper-evident validation audit log.

Activated by: OPENDQV_TRACE_LOG=true environment variable
Log path: OPENDQV_TRACE_LOG_PATH (default: opendqv_trace.jsonl)

Each entry is a newline-delimited JSON object:
{
    "ts": "2026-03-09T12:00:00.000Z",
    "contract": "patient_record",
    "context": "default",
    "record_index": 0,
    "valid": true,
    "error_count": 0,
    "warning_count": 0,
    "fields_validated": ["nhs_number", "date_of_birth"],
    "sensitive_fields_suppressed": ["national_id", "date_of_birth"],
    "prev_hash": "0000...0000",
    "entry_hash": "sha256(...)",
    "hmac": "hmac-sha256(...)"  (present only when OPENDQV_TRACE_HMAC_KEY is set)
}

NOTE: Field VALUES are never logged. Only field NAMES and validation outcomes.
"""

import hashlib
import hmac as _hmac_module
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GENESIS_HASH = "0" * 64
_trace_lock = threading.Lock()
_trace_last_hash: dict[str, str] = {}  # log_path -> last entry_hash

# Read HMAC key at module import time
_TRACE_HMAC_KEY: Optional[str] = os.environ.get("OPENDQV_TRACE_HMAC_KEY") or None

# ACT-004: Log rotation config
# OPENDQV_TRACE_LOG_MAX_SIZE_MB — rotate when file exceeds this size (0 = never rotate, default 100 MB)
# OPENDQV_TRACE_LOG_ROTATE — number of rotated segments to keep (default 5)
_TRACE_MAX_SIZE_BYTES: int = int(os.environ.get("OPENDQV_TRACE_LOG_MAX_SIZE_MB", "100")) * 1024 * 1024
_TRACE_ROTATE_KEEP: int = max(1, int(os.environ.get("OPENDQV_TRACE_LOG_ROTATE", "5")))

# Emit a startup warning if TRACE_LOG is enabled but HMAC key is not set
if os.environ.get("OPENDQV_TRACE_LOG", "").lower() in ("true", "1", "yes") and not _TRACE_HMAC_KEY:
    logger.warning(
        "TRACE_LOG is enabled but OPENDQV_TRACE_HMAC_KEY is not set. "
        "Entries are hash-chained but not HMAC-signed. "
        "An adversary with filesystem access could reconstruct a valid chain. "
        "Set OPENDQV_TRACE_HMAC_KEY to a cryptographically random secret for 21 CFR Part 11 / ISO 27001 deployments."
    )


def _compute_hmac(key: str, payload: str) -> str:
    """Compute HMAC-SHA256 of payload using key. Returns hex digest."""
    return _hmac_module.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _is_enabled() -> bool:
    return os.environ.get("OPENDQV_TRACE_LOG", "").lower() in ("true", "1", "yes")


def _get_log_path() -> Path:
    return Path(os.environ.get("OPENDQV_TRACE_LOG_PATH", "opendqv_trace.jsonl"))


def _compute_entry_hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256(f"{prev_hash}|{payload}".encode()).hexdigest()


def _rotate_if_needed(log_path: Path) -> None:
    """
    ACT-004: Rotate the trace log if it exceeds OPENDQV_TRACE_LOG_MAX_SIZE_MB.

    Rotation scheme: logfile → logfile.1 → logfile.2 … logfile.N (oldest deleted).
    Each rotated segment is a self-contained NDJSON file verifiable independently.
    The in-memory hash chain resets after rotation so the new segment starts from
    the genesis hash — this is intentional; verifiers process each segment separately.

    Must be called inside _trace_lock.
    """
    if _TRACE_MAX_SIZE_BYTES <= 0:
        return
    try:
        if not log_path.exists() or log_path.stat().st_size < _TRACE_MAX_SIZE_BYTES:
            return
    except OSError:
        return

    # Shift existing rotated segments: .5 deleted, .4 → .5, ..., .1 → .2
    for i in range(_TRACE_ROTATE_KEEP - 1, 0, -1):
        src = Path(f"{log_path}.{i}")
        dst = Path(f"{log_path}.{i + 1}")
        if src.exists():
            if dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass
            try:
                src.rename(dst)
            except OSError as exc:
                logger.error("TRACE_LOG rotation rename %s → %s failed: %s", src, dst, exc)

    # Rotate current log to .1
    rotated = Path(f"{log_path}.1")
    if rotated.exists():
        try:
            rotated.unlink()
        except OSError:
            pass
    try:
        log_path.rename(rotated)
    except OSError as exc:
        logger.error("TRACE_LOG rotation failed: %s", exc)
        return

    # Reset in-memory hash so new segment starts from genesis
    _trace_last_hash.pop(str(log_path), None)
    logger.info(
        "TRACE_LOG rotated: %s → %s (max_size=%dMB, keep=%d segments)",
        log_path, rotated, _TRACE_MAX_SIZE_BYTES // (1024 * 1024), _TRACE_ROTATE_KEEP,
    )


def write_trace_entry(
    contract_name: str,
    context: Optional[str],
    record_index: int,
    valid: bool,
    error_count: int,
    warning_count: int,
    fields_validated: list[str],
    sensitive_fields: list[str],
    failed_rules: list[str],
) -> None:
    """
    Write a single trace entry. Thread-safe. No-op if TRACE_LOG is not enabled.

    SECURITY: Field values are never logged — only field names and rule outcomes.
    Sensitive field names are noted in sensitive_fields_suppressed but their
    validation results are excluded from failed_rules.
    """
    if not _is_enabled():
        return

    log_path = _get_log_path()
    ts = datetime.now(timezone.utc).isoformat()
    sensitive_set = set(sensitive_fields or [])

    # Redact sensitive fields from failed_rules (don't reveal which sensitive fields failed)
    safe_failed_rules = [r for r in (failed_rules or []) if r not in sensitive_set]

    payload_obj = {
        "ts": ts,
        "contract": contract_name,
        "context": context or "default",
        "record_index": record_index,
        "valid": valid,
        "error_count": error_count,
        "warning_count": warning_count,
        "fields_validated": sorted(set(fields_validated or []) - sensitive_set),
        "sensitive_fields_suppressed": sorted(sensitive_set & set(fields_validated or [])),
        "failed_rules": safe_failed_rules,
    }
    payload_str = json.dumps(payload_obj, sort_keys=True, separators=(",", ":"))

    with _trace_lock:
        # ACT-004: Rotate first — so prev_hash reflects the start of the new segment
        _rotate_if_needed(log_path)

        prev_hash = _trace_last_hash.get(str(log_path), _GENESIS_HASH)
        entry_hash = _compute_entry_hash(prev_hash, payload_str)

        entry = {**payload_obj, "prev_hash": prev_hash, "entry_hash": entry_hash}

        # HMAC signing — if key is set, sign the entry (excluding the hmac field itself)
        hmac_key = os.environ.get("OPENDQV_TRACE_HMAC_KEY") or _TRACE_HMAC_KEY
        if hmac_key:
            entry_payload_str = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            entry["hmac"] = _compute_hmac(hmac_key, entry_payload_str)

        line = json.dumps(entry, separators=(",", ":")) + "\n"

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line)
            _trace_last_hash[str(log_path)] = entry_hash
        except OSError as exc:
            logger.error("TRACE_LOG write failed: %s", exc)


def verify_trace_log(log_path: Optional[str] = None) -> dict:
    """
    Verify the hash chain integrity of a trace log file.

    If OPENDQV_TRACE_HMAC_KEY is set, also verifies the HMAC signature on each entry.
    Entries without an "hmac" field are treated as pre-HMAC entries and skipped for
    HMAC verification (backward compatibility).

    Returns:
        {"valid": True, "entries": N, "hmac_verified": True/False, "hmac_key_present": True/False}
          if chain is intact
        {"valid": False, "broken_at": N, "entries": N, "error": "..."}  if tampered
    """
    path = Path(log_path) if log_path else _get_log_path()
    if not path.exists():
        return {"valid": True, "entries": 0, "message": "No log file found"}

    hmac_key = os.environ.get("OPENDQV_TRACE_HMAC_KEY") or _TRACE_HMAC_KEY
    hmac_key_present = bool(hmac_key)
    hmac_all_verified = True  # will be set False if any HMAC check fails or is skipped

    prev_hash = _GENESIS_HASH
    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                return {"valid": False, "broken_at": count, "entries": count, "error": f"JSON parse error: {e}"}

            stored_prev = entry.get("prev_hash", "")
            stored_hash = entry.get("entry_hash", "")

            if stored_prev != prev_hash:
                return {
                    "valid": False,
                    "broken_at": count,
                    "entries": count,
                    "error": f"prev_hash mismatch at entry {count}",
                }

            # Recompute hash from payload (exclude prev_hash, entry_hash, and hmac)
            payload_obj = {k: v for k, v in entry.items() if k not in ("prev_hash", "entry_hash", "hmac")}
            payload_str = json.dumps(payload_obj, sort_keys=True, separators=(",", ":"))
            expected_hash = _compute_entry_hash(prev_hash, payload_str)

            if expected_hash != stored_hash:
                return {
                    "valid": False,
                    "broken_at": count,
                    "entries": count,
                    "error": f"entry_hash mismatch at entry {count} — log may have been tampered",
                }

            # HMAC verification — only if key is present and entry has an hmac field
            stored_hmac = entry.get("hmac")
            if hmac_key and stored_hmac:
                # Recompute HMAC over the entry dict (without the hmac field itself)
                entry_without_hmac = {k: v for k, v in entry.items() if k != "hmac"}
                entry_str = json.dumps(entry_without_hmac, sort_keys=True, separators=(",", ":"))
                expected_hmac = _compute_hmac(hmac_key, entry_str)
                if not _hmac_module.compare_digest(expected_hmac, stored_hmac):
                    return {
                        "valid": False,
                        "broken_at": count,
                        "entries": count,
                        "error": f"HMAC mismatch at entry {count} — log may have been tampered",
                        "hmac_verified": False,
                        "hmac_key_present": hmac_key_present,
                    }
            elif hmac_key and not stored_hmac:
                # Entry is a pre-HMAC entry — skip HMAC check (backward compat)
                hmac_all_verified = False
            elif not hmac_key and stored_hmac:
                # HMAC present in log but no key configured — cannot verify
                hmac_all_verified = False

            prev_hash = stored_hash
            count += 1

    return {
        "valid": True,
        "entries": count,
        "hmac_verified": hmac_all_verified and hmac_key_present,
        "hmac_key_present": hmac_key_present,
    }
