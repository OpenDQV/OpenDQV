"""CRT168 PR-B — lookup_source additive + path-leak sweep tests.

Covers the v2.2.6 fix for the external review's P3.10:
the `explain_error` response and explanation strings must not expose the
server's internal `ref/<filename>.txt` filesystem layout. A logical
`lookup_source` field replaces the leaky path in user-facing copy.
"""

import re

from opendqv.core._uuid7 import uuid7  # noqa: F401  (sanity import)
from opendqv.core.explainer import _logical_lookup_source, explain_rule
from opendqv.core.rule_parser import Rule


def _make_lookup_rule(lookup_file: str | None) -> Rule:
    kwargs = {
        "name": "currency_valid",
        "type": "lookup",
        "field": "currency",
        "error_message": "currency must be in the reference list",
    }
    if lookup_file is not None:
        kwargs["lookup_file"] = lookup_file
    return Rule(**kwargs)


class TestLogicalLookupSource:
    def test_strips_ref_prefix_and_txt_suffix(self):
        assert _logical_lookup_source("ref/universal_currency.txt") == "universal_currency"

    def test_handles_no_ref_prefix(self):
        assert _logical_lookup_source("iso_country_alpha2.txt") == "iso_country_alpha2"

    def test_external_url_collapses_to_external_reference(self):
        assert _logical_lookup_source("https://example.com/codes.txt") == "external reference"
        assert _logical_lookup_source("http://internal.example/codes") == "external reference"

    def test_none_falls_back(self):
        assert _logical_lookup_source(None) == "reference list"
        assert _logical_lookup_source("") == "reference list"


class TestExplainerLookupOutput:
    def test_explanation_does_not_leak_filesystem_path(self):
        info = explain_rule(_make_lookup_rule("ref/universal_currency.txt"))
        explanation = info["explanation"]
        assert "ref/" not in explanation
        assert ".txt" not in explanation
        assert "/" not in explanation
        # Logical name shows up instead.
        assert "universal_currency" in explanation

    def test_lookup_source_present_on_lookup_rule(self):
        info = explain_rule(_make_lookup_rule("ref/iso_country_alpha2.txt"))
        assert info.get("lookup_source") == "iso_country_alpha2"

    def test_lookup_source_absent_on_non_lookup_rule(self):
        info = explain_rule(Rule(
            name="age_min",
            type="min",
            field="age",
            error_message="must be 18+",
            min_value=18,
        ))
        assert "lookup_source" not in info

    def test_constraint_lookup_file_unchanged(self):
        info = explain_rule(_make_lookup_rule("ref/universal_currency.txt"))
        assert info["constraint"]["lookup_file"] == "ref/universal_currency.txt"


class TestExplainErrorEndpoint:
    def test_endpoint_returns_lookup_source_and_clean_explanation(self, client, auth_headers):
        contracts = client.get("/api/v1/contracts").json()
        target = None
        for c in contracts:
            detail = client.get(f"/api/v1/contracts/{c['name']}").json()
            for r in detail["rules"]:
                if r["type"] == "lookup":
                    target = (c["name"], r["field"], r["name"])
                    break
            if target:
                break
        assert target, "expected at least one lookup rule across the bundled contracts"

        contract_name, field, rule_name = target
        r = client.get(
            f"/api/v1/contracts/{contract_name}/explain/{field}/{rule_name}",
            headers=auth_headers,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["rule_type"] == "lookup"
        assert "lookup_source" in data
        assert data["lookup_source"]
        assert not re.search(r"ref/|\.txt", data["explanation"])
