"""
v2.3.22 Cluster D — explain_error real examples + Rule reconstruction
on proxy path.

Persona B round-1 MCP verification appendix (2026-04-26) finding O-19:

> O-19 (`explain_error` templated content) — CONFIRMED for regex,
> REFUTED for min.
> Regex rule (valid_email):
>   "valid_examples": ["a value matching ^[a-zA-Z0-9_...]"]
>   "invalid_examples": ["(value not matching the pattern)", null, ""]
> The "examples" are placeholders, not real examples.
> Min rule (age_minimum):
>   "valid_examples": [0.0, 1, 100]   ← REAL
> O-19 is rule-type-specific. Regex and (likely) lookup/enum return
> placeholders; min/max returns real values.

Root cause traced in `mcp_server.py:_tool_explain_error`:

  Proxy branch (lines 1203-1252) reconstructs a minimal Rule object
  with only (name, type, field, error_message). Every CONSTRAINT
  field is dropped:
    pattern, min_value, max_value, min_length, max_length, format,
    compare_to, compare_op, min_age, max_age, allowed_values,
    lookup_file, checksum_algorithm, negate.

  Then explain_rule() is called on this stripped rule. With no
  pattern, _regex emits "a value matching {None}" placeholder.
  With no lookup_file, _lookup emits placeholders. With min_value,
  _min emits real numeric examples — exactly the rule type the
  reviewer found working.

  In-process branch (lines 1254-1290) uses contract.rules directly
  — full Rule objects with all constraints. explain_rule produces
  real examples.

Sonnet's pre-impl review (a08e81aeaf82ace59) confirmed:
  - REST GET /contracts/{name} already serializes all constraint
    fields (RuleInfo at models.py:231-252). No server-side change.
  - Fix is local: extend the _Rule(...) constructor call on the
    proxy branch to pass every constraint field from the API
    response.
  - REST aliases: `min` / `max` (RuleInfo) → `min_value` / `max_value`
    (Rule). Rule has populate_by_name=True so either form is safe.
  - One bug, one fix: real examples follow from full reconstruction.
  - N-1 mifid YYYY-MM-DD content is content-side, NOT this cluster.

Test pattern (Sonnet directive): seed contract with regex + min +
lookup rules. Call _tool_explain_error via (in-process, proxy).
Assert no placeholder strings in valid/invalid examples — that's
stable across future constraint changes.
"""

import asyncio
import json

import pytest


# ── Placeholder predicates (the strings that O-19 flagged) ─────────────

def _has_no_constraint_placeholder(examples) -> bool:
    """True if examples contain a "constraint was None" sentinel —
    the explain_rule output that reveals the proxy reconstruction
    dropped the constraint field. Specifically:

      "a value matching None"   ← regex with no pattern
      "(a value present in the reference list)" ← lookup with no file

    Engine-level synthesis quality (e.g. "a value matching ^foo$"
    being non-real even with pattern present) is a separate concern
    targeted for v2.4. Cluster D's narrow scope is closing the
    proxy-reconstruction drop, not upgrading example synthesis.
    """
    sentinels = (
        "matching None",
        "(a value present in the reference list)",
    )
    return any(
        isinstance(v, str) and any(s in v for s in sentinels)
        for v in examples
    )


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def seeded_explain_contract(monkeypatch, tmp_path):
    """Three-rule contract exercising every reviewer-named branch:
      - regex on `email` with a real pattern → in-process emits real
        synthesised values; proxy must do the same.
      - min on `age` with min_value=18 → already worked per reviewer
        (regression guard).
      - lookup on `country` against a small file → proxy must
        produce a real value from the file, not a placeholder.

    Uses the live registry's draft API so both branches read the
    same in-memory object via different paths.
    """
    from opendqv.core.rule_parser import Rule, Severity
    from opendqv.core.contracts import DataContract
    from opendqv import mcp_server
    _registry = mcp_server._registry

    lookup_file = tmp_path / "countries.txt"
    lookup_file.write_text("GB\nFR\nDE\n", encoding="utf-8")

    rules = [
        Rule(
            name="email_pattern", type="regex", field="email",
            pattern=r"^[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}$",
            error_message="Email must match standard pattern.",
            severity=Severity.ERROR,
        ),
        Rule(
            name="age_minimum", type="min", field="age",
            min_value=18.0,
            error_message="Age must be at least 18.",
            severity=Severity.ERROR,
        ),
        Rule(
            name="country_lookup", type="lookup", field="country",
            lookup_file=str(lookup_file),
            error_message="Country must be a known ISO code.",
            severity=Severity.ERROR,
        ),
    ]
    contract = DataContract(
        name="explain_test", version="1.0", description="Cluster D fixture",
        owner="test", rules=rules,
    )
    _registry._contracts["explain_test"] = {"1.0": contract}
    yield contract
    _registry._contracts.pop("explain_test", None)


# ── In-process baseline ────────────────────────────────────────────────
#
# Engine-level behavior baseline (the v2.4 synthesis upgrade target):
#   - min/max: emit real numeric examples derived from min_value/max_value
#     (already worked per reviewer's REFUTED branch of O-19)
#   - regex: emits "a value matching {pattern}" — pattern is real, but
#     the example string is templated, not a synthesised matching value.
#     Reviewer's CONFIRMED branch. Engine limitation; v2.4 capability.
#   - lookup: emits "(a value present in the reference list)" regardless
#     of whether lookup_file is set. Engine limitation; v2.4 capability.
#
# Cluster D's narrow scope: ensure the proxy-path Rule reconstruction
# carries every constraint field through, so the engine's _explain
# branches see the same input the in-process path sees. Real-value
# synthesis is a separate v2.4 item logged in project_v240_known_ceiling.md.

class TestExplainErrorInProcessBaseline:
    """In-process baseline — what the engine produces today with full
    constraints. Cluster D's parity assertions are anchored here."""

    def test_min_rule_real_examples(self, seeded_explain_contract, monkeypatch):
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_explain_error({
            "contract": "explain_test", "field": "age", "rule": "age_minimum",
        }))
        body = json.loads(result[0].text)
        assert not _has_no_constraint_placeholder(body["valid_examples"]), body
        assert any(isinstance(v, (int, float)) and v >= 18 for v in body["valid_examples"])

    def test_regex_rule_in_process_pattern_in_constraint(
        self, seeded_explain_contract, monkeypatch
    ):
        """Pattern flows into the constraint payload. Engine-level
        example synthesis is a v2.4 concern; what v2.3.22 cares
        about is that the constraint is faithfully present."""
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_explain_error({
            "contract": "explain_test", "field": "email", "rule": "email_pattern",
        }))
        body = json.loads(result[0].text)
        # NOT the "matching None" no-constraint sentinel — pattern
        # was passed through.
        assert not _has_no_constraint_placeholder(body["valid_examples"]), body
        assert body["constraint"].get("pattern"), body

    def test_lookup_rule_in_process_lookup_file_in_constraint(
        self, seeded_explain_contract, monkeypatch
    ):
        """lookup_file flows into the constraint payload."""
        from opendqv import mcp_server
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        result = asyncio.run(mcp_server._tool_explain_error({
            "contract": "explain_test", "field": "country", "rule": "country_lookup",
        }))
        body = json.loads(result[0].text)
        assert body["constraint"].get("lookup_file"), body


# ── Proxy path (the bug — should be red pre-fix) ───────────────────────

class TestExplainErrorProxyReconstruction:
    """Proxy path drops constraint fields from Rule reconstruction →
    explain_rule emits placeholders. This is O-19 P2."""

    def _setup_proxy(self, monkeypatch, contract):
        """Mock _remote_client.get to return the same contract data
        the REST endpoint would, with full RuleInfo serialisation."""
        from opendqv import mcp_server
        from unittest.mock import MagicMock

        client = MagicMock()
        # Build the RuleInfo-shaped dicts the REST API would emit.
        # RuleInfo uses `min` / `max` aliases — match that.
        rules_payload = []
        for r in contract.rules:
            rules_payload.append({
                "name": r.name,
                "type": r.type,
                "field": r.field,
                "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                "error_message": r.error_message,
                "pattern": r.pattern,
                "min": r.min_value,
                "max": r.max_value,
                "min_length": r.min_length,
                "max_length": r.max_length,
                "format": r.format,
                "compare_to": r.compare_to,
                "compare_op": r.compare_op,
                "min_age": r.min_age,
                "max_age": r.max_age,
                "allowed_values": r.allowed_values,
                "lookup_file": r.lookup_file,
                "checksum_algorithm": r.checksum_algorithm,
            })
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "name": contract.name, "version": contract.version,
            "rules": rules_payload,
        }
        resp.raise_for_status = MagicMock()
        client.get.return_value = resp
        monkeypatch.setattr(mcp_server, "_remote_client", client)

    def test_proxy_regex_carries_pattern_through(
        self, seeded_explain_contract, monkeypatch
    ):
        """v2.3.22 Cluster D / O-19 P2 narrow scope: proxy
        reconstruction must pass `pattern` so the constraint payload
        is faithful — not the "matching None" no-constraint sentinel.
        Real example synthesis is the v2.4 follow-on."""
        from opendqv import mcp_server
        self._setup_proxy(monkeypatch, seeded_explain_contract)
        result = asyncio.run(mcp_server._tool_explain_error({
            "contract": "explain_test", "field": "email", "rule": "email_pattern",
        }))
        body = json.loads(result[0].text)
        assert not _has_no_constraint_placeholder(body["valid_examples"]), (
            f"v2.3.22 Cluster D regression: proxy emitted "
            f"'matching None' for regex rule. Root cause: proxy "
            f"reconstructs Rule without pattern. Fix: pass full "
            f"constraint set. Got: {body['valid_examples']}"
        )
        assert body["constraint"].get("pattern"), (
            f"Proxy must carry pattern through to constraint payload. "
            f"Got: {body.get('constraint')}"
        )

    def test_proxy_min_rule_carries_min_value_through(
        self, seeded_explain_contract, monkeypatch
    ):
        """min/max worked per reviewer's REFUTED branch — guard."""
        from opendqv import mcp_server
        self._setup_proxy(monkeypatch, seeded_explain_contract)
        result = asyncio.run(mcp_server._tool_explain_error({
            "contract": "explain_test", "field": "age", "rule": "age_minimum",
        }))
        body = json.loads(result[0].text)
        assert not _has_no_constraint_placeholder(body["valid_examples"]), body
        assert any(
            isinstance(v, (int, float)) and v >= 18
            for v in body["valid_examples"]
        ), f"min rule on proxy must surface real >= 18 value. Got: {body['valid_examples']}"

    def test_proxy_lookup_carries_lookup_file_through(
        self, seeded_explain_contract, monkeypatch
    ):
        """Proxy must reconstruct lookup_file so the constraint is
        faithful. Engine still emits placeholder examples (v2.4
        capability target); but constraint must reflect reality —
        the actual file path, not the "(reference list)" fallback
        that _lookup substitutes when lookup_file is None."""
        from opendqv import mcp_server
        self._setup_proxy(monkeypatch, seeded_explain_contract)
        result = asyncio.run(mcp_server._tool_explain_error({
            "contract": "explain_test", "field": "country", "rule": "country_lookup",
        }))
        body = json.loads(result[0].text)
        lookup_file = body["constraint"].get("lookup_file")
        # The expected lookup_file is the tmp_path file from the fixture.
        # Engine's None-fallback is "(reference list)" — must NOT match.
        assert lookup_file and lookup_file != "(reference list)", (
            f"v2.3.22 Cluster D regression: proxy reconstruction "
            f"dropped lookup_file. _lookup substituted the None "
            f"fallback. Got constraint: {body.get('constraint')}"
        )
        assert "countries.txt" in lookup_file, (
            f"Proxy lookup_file must match the fixture path. "
            f"Got: {lookup_file!r}"
        )


# ── Cross-path parity (CRT170 symmetric-edit discipline) ───────────────

class TestExplainErrorPathParity:
    """The reviewer pattern that has burned this team three times
    (v2.2.7 stale uvicorn, v2.2.8 stale proxy, v2.3.x stale-historical
    hash) — proxy and in-process must agree on response structure
    for the same input. Add the cold-client smoke gap closure here."""

    def test_proxy_and_in_process_emit_same_keys(
        self, seeded_explain_contract, monkeypatch
    ):
        from opendqv import mcp_server
        from unittest.mock import MagicMock

        # In-process call.
        monkeypatch.setattr(mcp_server, "_remote_client", None)
        in_process_result = asyncio.run(mcp_server._tool_explain_error({
            "contract": "explain_test", "field": "email", "rule": "email_pattern",
        }))
        in_process_body = json.loads(in_process_result[0].text)

        # Proxy call (same data via REST mock).
        client = MagicMock()
        rules_payload = [{
            "name": r.name, "type": r.type, "field": r.field,
            "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
            "error_message": r.error_message,
            "pattern": r.pattern, "min": r.min_value, "max": r.max_value,
            "min_length": r.min_length, "max_length": r.max_length,
            "format": r.format, "compare_to": r.compare_to,
            "compare_op": r.compare_op, "min_age": r.min_age,
            "max_age": r.max_age, "allowed_values": r.allowed_values,
            "lookup_file": r.lookup_file,
            "checksum_algorithm": r.checksum_algorithm,
        } for r in seeded_explain_contract.rules]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "name": "explain_test", "version": "1.0", "rules": rules_payload,
        }
        resp.raise_for_status = MagicMock()
        client.get.return_value = resp
        monkeypatch.setattr(mcp_server, "_remote_client", client)

        proxy_result = asyncio.run(mcp_server._tool_explain_error({
            "contract": "explain_test", "field": "email", "rule": "email_pattern",
        }))
        proxy_body = json.loads(proxy_result[0].text)

        assert set(in_process_body.keys()) == set(proxy_body.keys()), (
            f"Path parity broken — in-process keys: {set(in_process_body.keys())}, "
            f"proxy keys: {set(proxy_body.keys())}"
        )
        assert in_process_body["rule_type"] == proxy_body["rule_type"]
        # Both paths must carry the constraint through identically.
        assert in_process_body.get("constraint") == proxy_body.get("constraint"), (
            f"Constraint payload must be identical across paths. "
            f"in-process: {in_process_body.get('constraint')}, "
            f"proxy: {proxy_body.get('constraint')}"
        )
        # Neither path may emit the no-constraint sentinel.
        assert not _has_no_constraint_placeholder(proxy_body["valid_examples"])
        assert not _has_no_constraint_placeholder(in_process_body["valid_examples"])
