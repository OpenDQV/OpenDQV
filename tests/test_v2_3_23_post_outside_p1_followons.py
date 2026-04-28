"""
v2.3.23 outside-review fix #3 follow-on + get_audit_event doc honesty.

Inside-view agent (a9fa65e1b83c1866f) caught two follow-on P1s after
the type-mismatch + persisted: false fixes shipped:

P1-A: TYPE_MISMATCH error has stale `suggested_fix` ("Increase the
  value to meet the minimum threshold."). The error_code+message
  swap was correct but suggested_fix is added downstream by
  _add_suggested_fixes which keys off rule.type — so a non-numeric
  string against a min rule still surfaces the min-violation hint.
  Engineer reads "Increase the value" → fixes the wrong thing.

P1-B: get_audit_event description says "primary key for audit replay
  and dispute resolution" without warning that 404 is the typical
  outcome for MCP-driven validates (which hardcode dry_run=True per
  CRT165). Inside agent missed the persisted:false flag (P1-6 ship)
  and read the 404 as a bug.

Sonnet's pre-impl review (ae22a28117b2d4f7b): bundle. P1-B is doc-
only (zero risk); P1-A is one branch in _add_suggested_fixes.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest


def _load_proxy_module():
    os.environ.setdefault("OPENDQV_API_URL", "http://127.0.0.1:1")
    os.environ.setdefault("OPENDQV_API_TOKEN", "")
    proxy_path = Path(__file__).resolve().parent.parent / "opendqv_mcp_proxy.py"
    spec = importlib.util.spec_from_file_location("opendqv_mcp_proxy", proxy_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["opendqv_mcp_proxy"] = mod
    spec.loader.exec_module(mod)
    return mod


def _in_process_descs():
    import asyncio
    from opendqv.mcp_server import server
    from mcp.types import ListToolsRequest
    handlers = server.request_handlers
    result = asyncio.run(
        handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
    )
    return {t.name: (t.description or "") for t in result.root.tools}


@pytest.fixture
def proxy_mod():
    return _load_proxy_module()


# ── P1-A: TYPE_MISMATCH suggested_fix is a coercion hint ───────────────

class TestTypeMismatchSuggestedFix:
    def test_type_mismatch_suggested_fix_is_coercion_hint(self):
        """Direct unit test of _add_suggested_fixes per Sonnet's
        directive — pinned to the exact bug site, no HTTP needed."""
        from opendqv.api.deps import _add_suggested_fixes
        from opendqv.core.rule_parser import Rule, Severity

        rules = [
            Rule(
                name="age_minimum", field="age", type="min", min_value=18.0,
                severity=Severity.ERROR, error_message="age >= 18",
            ),
        ]
        type_mismatch_error = {
            "field": "age", "rule": "age_minimum",
            "message": "min rule on field 'age' expected numeric value, got str",
            "severity": "error",
            "error_code": "OPENDQV_TYPE_MISMATCH",
        }
        result = _add_suggested_fixes([type_mismatch_error], rules)
        fix = result[0].get("suggested_fix") or ""
        # Must be a coercion hint, not the rule's min-violation hint.
        assert "increase" not in fix.lower(), (
            f"v2.3.23 outside follow-on P1-A: TYPE_MISMATCH suggested_fix "
            f"must NOT inherit the min-rule's 'Increase the value' hint. "
            f"Engineer reads it and fixes the wrong thing. Got: {fix!r}"
        )
        assert "decrease" not in fix.lower(), (
            f"TYPE_MISMATCH must not surface max-rule's 'Decrease' hint. "
            f"Got: {fix!r}"
        )
        assert "coerce" in fix.lower() or "numeric" in fix.lower() or "type" in fix.lower(), (
            f"TYPE_MISMATCH suggested_fix must be a coercion hint. "
            f"Got: {fix!r}"
        )
        # Must mention the offending Python type so the engineer knows
        # what they sent.
        assert "str" in fix, fix

    def test_legitimate_min_violation_keeps_min_hint(self):
        """Regression guard: a real value-below-threshold violation
        must still get the rule's own suggested_fix, not the
        type-mismatch hint."""
        from opendqv.api.deps import _add_suggested_fixes
        from opendqv.core.rule_parser import Rule, Severity

        rules = [
            Rule(
                name="age_minimum", field="age", type="min", min_value=18.0,
                severity=Severity.ERROR, error_message="age must be >= 18",
            ),
        ]
        real_min_violation = {
            "field": "age", "rule": "age_minimum",
            "message": "age must be >= 18",
            "severity": "error",
            "error_code": "OPENDQV_MIN_AGE_MINIMUM",
        }
        result = _add_suggested_fixes([real_min_violation], rules)
        fix = result[0].get("suggested_fix") or ""
        # Real min violation → should still be a min-violation hint.
        assert "coerce" not in fix.lower(), (
            f"Real min violation must NOT get the type-coercion hint. "
            f"Got: {fix!r}"
        )


# ── P1-B: get_audit_event description warns about 404 on dry_run ──────

class TestGetAuditEventDescriptionWarnsOnNonPersisted:
    def test_in_process_description_mentions_persisted_or_404(self):
        descs = _in_process_descs()
        desc = descs.get("get_audit_event", "").lower()
        # Description must explain when 404 fires (dry_run / not
        # persisted) so a consumer doesn't read 404 as a bug.
        assert "persisted" in desc or "dry_run" in desc or "404" in desc, (
            f"v2.3.23 outside follow-on P1-B: get_audit_event description "
            f"must warn that 404 is the typical outcome for non-persisted "
            f"events (e.g. all MCP-driven validates). Inside agent read "
            f"404 as a bug; outside reviewer same risk. Got: {desc!r}"
        )

    def test_proxy_description_mentions_persisted_or_404(self, proxy_mod):
        tool = next(t for t in proxy_mod.TOOLS if t["name"] == "get_audit_event")
        desc = tool.get("description", "").lower()
        assert "persisted" in desc or "dry_run" in desc or "404" in desc, desc
