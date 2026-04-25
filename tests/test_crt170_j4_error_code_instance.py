"""
tests/test_crt170_j4_error_code_instance.py — CRT170/J4 acceptance.

Pins the rule-instance-shaped error_code semantic introduced in v2.3.6.

Before v2.3.6:
    Two regex rules (e.g., valid_email and valid_phone) both produced
    OPENDQV_REGEX_001. Consumers using error_code as a routing key
    could not distinguish "bad email" from "bad phone number" — the
    response field's value did not reflect what its name claimed.

From v2.3.6:
    error_code = OPENDQV_<TYPE_UPPER>_<RULE_NAME_UPPER>
    valid_email   → OPENDQV_REGEX_VALID_EMAIL
    valid_phone   → OPENDQV_REGEX_VALID_PHONE
    name_required → OPENDQV_NOT_EMPTY_NAME_REQUIRED

Working principle (CRT170, extends J1, J3, J6): a response field's value
must reflect what its name claims. error_code claims to identify the
specific rule that failed; it now does.
"""
from fastapi.testclient import TestClient

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_record, validate_batch


# ── Rule-level cached_error_code shape ─────────────────────────────────

class TestCachedErrorCodeShape:

    def test_includes_rule_name_segment(self):
        rule = Rule(
            name="valid_email",
            field="email",
            type="regex",
            pattern=r"^[^@]+@[^@]+\.[^@]+$",
            error_message="bad email",
        )
        assert rule.cached_error_code == "OPENDQV_REGEX_VALID_EMAIL"

    def test_two_rules_same_type_get_different_codes(self):
        email_rule = Rule(
            name="valid_email", field="email", type="regex",
            pattern=r"^[^@]+@[^@]+$", error_message="bad email",
        )
        phone_rule = Rule(
            name="valid_phone", field="phone", type="regex",
            pattern=r"^\+\d+$", error_message="bad phone",
        )
        assert email_rule.cached_error_code != phone_rule.cached_error_code
        assert email_rule.cached_error_code == "OPENDQV_REGEX_VALID_EMAIL"
        assert phone_rule.cached_error_code == "OPENDQV_REGEX_VALID_PHONE"

    def test_not_empty_rule_shape(self):
        rule = Rule(
            name="name_required", field="name", type="not_empty",
            error_message="required",
        )
        assert rule.cached_error_code == "OPENDQV_NOT_EMPTY_NAME_REQUIRED"

    def test_range_rule_shape(self):
        rule = Rule(
            name="age_range", field="age", type="range",
            min_value=0, max_value=120, error_message="bad age",
        )
        assert rule.cached_error_code == "OPENDQV_RANGE_AGE_RANGE"

    def test_codes_stable_across_construction(self):
        """Same name + type → same code, regardless of when the Rule is built."""
        a = Rule(name="x", field="f", type="regex", pattern="^.*$", error_message="m")
        b = Rule(name="x", field="f", type="regex", pattern="^.*$", error_message="m")
        assert a.cached_error_code == b.cached_error_code


# ── Single-record path ─────────────────────────────────────────────────

class TestSingleRecordPath:

    def test_emits_rule_instance_code_on_failure(self):
        rules = [
            Rule(name="valid_email", field="email", type="regex",
                 pattern=r"^[^@]+@[^@]+\.[^@]+$", error_message="bad email"),
        ]
        result = validate_record({"email": "not-an-email"}, rules)
        assert not result["valid"]
        assert result["errors"][0]["error_code"] == "OPENDQV_REGEX_VALID_EMAIL"

    def test_two_failing_rules_get_distinct_codes(self):
        rules = [
            Rule(name="valid_email", field="email", type="regex",
                 pattern=r"^[^@]+@[^@]+\.[^@]+$", error_message="bad email"),
            Rule(name="valid_phone", field="phone", type="regex",
                 pattern=r"^\+\d{6,}$", error_message="bad phone"),
        ]
        result = validate_record({"email": "bad", "phone": "also bad"}, rules)
        codes = {e["error_code"] for e in result["errors"]}
        assert codes == {"OPENDQV_REGEX_VALID_EMAIL", "OPENDQV_REGEX_VALID_PHONE"}


# ── Batch (DuckDB) path ────────────────────────────────────────────────

class TestBatchPath:

    def test_batch_emits_rule_instance_code(self):
        rules = [
            Rule(name="valid_email", field="email", type="regex",
                 pattern=r"^[^@]+@[^@]+\.[^@]+$", error_message="bad email"),
        ]
        results = validate_batch([{"email": "bad"}], rules)
        assert results["results"][0]["errors"][0]["error_code"] == "OPENDQV_REGEX_VALID_EMAIL"

    def test_batch_and_single_path_agree(self):
        """Same record + rules through both paths must produce the same error_code."""
        rules = [
            Rule(name="valid_email", field="email", type="regex",
                 pattern=r"^[^@]+@[^@]+\.[^@]+$", error_message="bad"),
        ]
        single = validate_record({"email": "bad"}, rules)
        batch = validate_batch([{"email": "bad"}], rules)
        assert (
            single["errors"][0]["error_code"]
            == batch["results"][0]["errors"][0]["error_code"]
        )


# ── REST API surface ───────────────────────────────────────────────────

class TestRestApiSurface:

    def test_validate_returns_rule_instance_code(self, client: TestClient, auth_headers):
        resp = client.post(
            "/api/v1/validate",
            json={"record": {"email": "bad", "age": 25}, "contract": "customer"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        errors = resp.json()["errors"]
        for err in errors:
            code = err["error_code"]
            assert code.endswith(f"_{err['rule'].upper()}"), (
                f"error_code {code!r} does not encode rule name {err['rule']!r}"
            )

    def test_two_distinct_failures_have_distinct_codes(self, client: TestClient, auth_headers):
        """A record failing two different rules must produce two different codes."""
        resp = client.post(
            "/api/v1/validate",
            json={
                "record": {"name": "", "email": "still-bad", "age": 25},
                "contract": "customer",
            },
            headers=auth_headers,
        )
        errors = resp.json()["errors"]
        assert len(errors) >= 2
        # No two errors with different rule names should share a code.
        rule_to_code: dict = {}
        for err in errors:
            rule_to_code.setdefault(err["rule"], err["error_code"])
            assert rule_to_code[err["rule"]] == err["error_code"]
        distinct_rules = {e["rule"] for e in errors}
        distinct_codes = {e["error_code"] for e in errors if e["rule"] in distinct_rules}
        assert len(distinct_codes) == len(distinct_rules), (
            f"Distinct rules collapsed to fewer codes: rules={distinct_rules} codes={distinct_codes}"
        )

    def test_no_legacy_001_suffix_in_response(self, client: TestClient, auth_headers):
        """Regression guard: no error_code ends in '_001' — the legacy collapsed shape."""
        resp = client.post(
            "/api/v1/validate",
            json={"record": {"email": "bad"}, "contract": "customer"},
            headers=auth_headers,
        )
        for err in resp.json()["errors"]:
            assert not err["error_code"].endswith("_001"), (
                f"Legacy _001 suffix leaked: {err['error_code']!r}"
            )
