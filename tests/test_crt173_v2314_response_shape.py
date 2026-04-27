"""
tests/test_crt173_v2314_response_shape.py — CRT173 v2.3.14 response shape polish.

Pins the response-shape changes shipped in v2.3.14:

  Item 20: pass_rate_ratio (0-1, 4dp) emitted alongside legacy pass_rate
           (percent, 1dp) on every quality surface. The dual-shape keeps wire
           compat while making the unit explicit at the field name.

  Item 21: contract_hash field descriptions across the API marked
           "DEPRECATED v2.3.14, removed v2.4 — alias of entry_hash". The
           wire shape is preserved; the description is the contract.

  Item 22: window field semantics documented in the model — window_hours is
           the canonical caller-requested window; effective_window_seconds
           reports actual data coverage; requested_window_hours is deprecated.

  Item 23: confidence_note is always populated (medium/high return ""
           instead of None). quality_confidence() returns tuple[str, str].

  Item 24: MCP errors return a structured envelope with
           {error: {error_code, kind, status, detail, remediation}} — no more
           bare {"error": "..."} or "Error: {exc}" stringifications.

  Item 25: validate response always populates `mode` and `would_have_failed`
           in both modes. record_id is echoed when caller provides it.
"""

import json
from pathlib import Path

import pytest


# ── Item 20: pass_rate field unification (v2.3.18 Q3) ─────────────────
#
# Original v2.3.14 shipped pass_rate (percent) AND pass_rate_ratio (0-1)
# side-by-side as a transitional dual-shape. v2.3.18 Q3 closes that
# transition: single canonical pass_rate_pct. The legacy `pass_rate` and
# `pass_rate_ratio` fields are removed entirely. These tests assert the
# new shape; the original TestPassRateRatio class is deleted.


class TestPassRatePctOnly:
    """v2.3.18 Q3: get_summary emits pass_rate_pct only — no `pass_rate`,
    no `pass_rate_ratio`."""

    def test_get_summary_emits_pass_rate_pct_only(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        s.record(contract="demo", context="x", valid=True, error_count=0, warning_count=0, latency_ms=1.0)
        s.record(contract="demo", context="x", valid=False, error_count=1, warning_count=0, latency_ms=1.0)
        summary = s.get_summary()
        assert "pass_rate_pct" in summary
        assert "pass_rate" not in summary, \
            "v2.3.18 Q3: bare `pass_rate` field is removed everywhere"
        assert "pass_rate_ratio" not in summary, \
            "v2.3.18 Q3: `pass_rate_ratio` companion is removed everywhere"
        # 1 of 2 valid → 50.0 percent
        assert summary["pass_rate_pct"] == pytest.approx(50.0, abs=0.1)

    def test_pass_rate_pct_is_zero_to_one_hundred_range(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        for _ in range(10):
            s.record(contract="demo", context="x", valid=True, error_count=0, warning_count=0, latency_ms=1.0)
        summary = s.get_summary()
        assert 0.0 <= summary["pass_rate_pct"] <= 100.0


# ── Item 23: confidence_note always populated ─────────────────────────


class TestConfidenceNoteAlwaysPopulated:
    """quality_confidence returns tuple[str, str] — note is "" not None."""

    def test_high_confidence_returns_empty_string_not_none(self):
        from opendqv.core.quality_stats import quality_confidence
        confidence, note = quality_confidence(2000)
        assert confidence == "high"
        assert note == ""
        assert isinstance(note, str)

    def test_medium_confidence_returns_empty_string_not_none(self):
        from opendqv.core.quality_stats import quality_confidence
        confidence, note = quality_confidence(50)
        assert confidence == "medium"
        assert note == ""
        assert isinstance(note, str)

    def test_low_confidence_returns_descriptive_string(self):
        from opendqv.core.quality_stats import quality_confidence
        confidence, note = quality_confidence(5)
        assert confidence == "low"
        assert note  # non-empty
        assert isinstance(note, str)

    def test_no_data_confidence_returns_descriptive_string(self):
        from opendqv.core.quality_stats import quality_confidence
        confidence, note = quality_confidence(0)
        assert confidence == "no_data"
        assert note
        assert isinstance(note, str)


# ── Item 24: structured MCP error envelope ────────────────────────────


class TestErrorEnvelope:
    """MCP error envelope replaces bare {"error": str} dicts."""

    def test_envelope_shape(self):
        from opendqv.mcp_server import _error_envelope
        out = json.loads(_error_envelope(
            error_code="TEST_CODE",
            kind="bad_request",
            detail="some detail",
            status=400,
            remediation="do this",
        ))
        assert out == {
            "error": {
                "error_code": "TEST_CODE",
                "kind": "bad_request",
                "status": 400,
                "detail": "some detail",
                "remediation": "do this",
            },
        }

    def test_envelope_default_remediation_empty(self):
        from opendqv.mcp_server import _error_envelope
        out = json.loads(_error_envelope(
            error_code="X", kind="internal", detail="boom", status=500,
        ))
        assert out["error"]["remediation"] == ""

    def test_no_loose_error_dicts_remain_in_mcp_server(self):
        """Regression guard: only the helper itself should emit json.dumps({"error": ...})."""
        src = Path("opendqv/mcp_server.py").read_text(encoding="utf-8")
        # Count occurrences of the pattern outside the helper definition.
        # The helper is the canonical site (lines ~118-126); every other
        # occurrence is a regression to the old bare-dict shape.
        loose_pattern = 'json.dumps({\n            "error":'
        assert loose_pattern not in src, (
            "Found a bare json.dumps({'error': ...}) site — use _error_envelope()"
        )


# ── Item 25: validate response always populates mode + would_have_failed ──


class TestValidateResponseDeterministicShape:
    """mode and would_have_failed are always populated, in both modes."""

    def test_enforcement_mode_populates_both_fields(self, client, auth_headers):
        body = {
            "record": {"email": "bad-email", "age": -5, "name": ""},
            "contract": "customer",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert data["mode"] == "enforcement"
        assert data["would_have_failed"] is True

    def test_enforcement_mode_passing_record(self, client, auth_headers):
        body = {
            "record": {
                "email": "alice@example.com",
                "age": 25,
                "name": "Alice",
                "password": "securepass123",
            },
            "contract": "customer",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["mode"] == "enforcement"
        assert data["would_have_failed"] is False

    def test_batch_enforcement_mode_populates_both_fields(self, client, auth_headers):
        body = {
            "records": [
                {"email": "a@b.com", "age": 25, "name": "Alice"},
                {"email": "bad-email", "age": -5, "name": ""},
            ],
            "contract": "customer",
        }
        r = client.post("/api/v1/validate/batch", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "enforcement"
        assert data["would_have_failed"] is True
        assert data["summary"]["failed"] > 0

    def test_record_id_echoed_when_provided(self, client, auth_headers):
        body = {
            "record": {
                "email": "alice@example.com",
                "age": 25,
                "name": "Alice",
                "password": "securepass123",
            },
            "contract": "customer",
            "record_id": "sf-lead-12345",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["record_id"] == "sf-lead-12345"

    def test_record_id_null_when_not_provided(self, client, auth_headers):
        body = {
            "record": {
                "email": "alice@example.com",
                "age": 25,
                "name": "Alice",
                "password": "securepass123",
            },
            "contract": "customer",
        }
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data.get("record_id") is None
