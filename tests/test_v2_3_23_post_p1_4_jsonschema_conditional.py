"""
v2.3.23 P1-4 — JSON Schema export emits `if/then` for conditional rules.

Persona B 2026-04-28: "JSON Schema export shows buyer_id with a flat
LEI regex, but the runtime correctly skips that regex when
buyer_id_type ≠ lei. Customer impact: a producer that bootstraps
validation from get_contract_jsonschema will reject valid records
that the OpenDQV runtime would accept, causing false-positive
rejections at the data source."

Sonnet's pre-impl review (aa97290c8fc2e575f): emit JSON Schema
2020-12 if/then[/else] blocks at root (`allOf`) for rules with
`condition`. {field, value} → if/then; {field, not_value} → if/then
with empty then + constraint in else. Unconditional rules stay in
`properties` exactly as today.

Five-case matrix per Sonnet's directive plus "no allOf when no
conditional rules" guard.
"""

import pytest

try:
    import jsonschema as js
except ImportError:
    js = None


@pytest.fixture
def conditional_contract():
    """Synthetic contract with one conditional regex rule:
    'lei_pattern' fires only when 'id_type' == 'lei'."""
    from opendqv.core.contracts import DataContract
    from opendqv.core.rule_parser import Rule, Severity

    return DataContract(
        name="conditional_test", version="1.0",
        rules=[
            Rule(
                name="lei_pattern",
                field="party_id",
                type="regex",
                pattern=r"^[A-Z0-9]{18}[0-9]{2}$",
                condition={"field": "id_type", "value": "lei"},
                severity=Severity.ERROR,
                error_message="LEI must match shape when id_type=lei",
            ),
        ],
    )


@pytest.fixture
def not_value_contract():
    """Conditional rule with not_value: 'no_special_chars' fires
    UNLESS region is 'INTERNAL'."""
    from opendqv.core.contracts import DataContract
    from opendqv.core.rule_parser import Rule, Severity

    return DataContract(
        name="notvalue_test", version="1.0",
        rules=[
            Rule(
                name="no_special_chars",
                field="customer_name",
                type="regex",
                pattern=r"^[A-Za-z ]+$",
                condition={"field": "region", "not_value": "INTERNAL"},
                severity=Severity.ERROR,
                error_message="Disallowed chars in non-internal customer names",
            ),
        ],
    )


@pytest.fixture
def unconditional_contract():
    """Contract with no conditional rules — for the "no allOf" guard."""
    from opendqv.core.contracts import DataContract
    from opendqv.core.rule_parser import Rule, Severity

    return DataContract(
        name="unconditional_test", version="1.0",
        rules=[
            Rule(
                name="email_pattern", field="email", type="regex",
                pattern=r"^[\w.-]+@[\w.-]+\.\w+$",
                severity=Severity.ERROR,
                error_message="Invalid email",
            ),
        ],
    )


class TestConditionalRulesEmitIfThen:
    def test_conditional_rule_emits_allof_with_if_then(self, conditional_contract):
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(conditional_contract)
        assert "allOf" in schema, (
            f"v2.3.23 P1-4: contract with conditional rule must emit "
            f"`allOf` block at root. Got keys: {list(schema.keys())}"
        )
        all_of = schema["allOf"]
        assert len(all_of) == 1, all_of
        block = all_of[0]
        assert "if" in block and "then" in block, block
        # Condition: id_type == lei
        if_props = block["if"].get("properties", {})
        assert "id_type" in if_props
        assert if_props["id_type"].get("const") == "lei"
        # Then: party_id has the regex
        then_props = block["then"].get("properties", {})
        assert "party_id" in then_props
        assert then_props["party_id"].get("pattern") == r"^[A-Z0-9]{18}[0-9]{2}$"

    def test_conditional_rule_NOT_in_top_level_properties(self, conditional_contract):
        """The conditional regex must NOT appear unconditionally on
        `party_id` in the top-level properties — that was the reviewer's
        exact bug. The pattern lives inside the `then` branch, not on
        the property directly."""
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(conditional_contract)
        party_prop = schema.get("properties", {}).get("party_id", {})
        # If pattern was emitted at top level, it would falsely reject
        # records where id_type != lei. Reviewer's exact concern.
        assert "pattern" not in party_prop, (
            f"v2.3.23 P1-4 regression: conditional regex leaked into "
            f"top-level properties.party_id. Producer using this schema "
            f"would over-reject records where id_type != lei. "
            f"Got party_id: {party_prop}"
        )

    @pytest.mark.skipif(js is None, reason="jsonschema library not installed")
    def test_jsonschema_validator_accepts_when_condition_does_not_match(
        self, conditional_contract
    ):
        """Validate via the jsonschema library: a record with
        id_type='national' and a non-LEI-shaped party_id must be
        ACCEPTED (the regex doesn't apply). Outcome-coupled — proves
        the conditional logic flows through to schema validators."""
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(conditional_contract)
        record = {"id_type": "national", "party_id": "GB-NI-AB123456C"}
        # Should NOT raise — pattern only applies when id_type=lei.
        js.validate(instance=record, schema=schema)

    @pytest.mark.skipif(js is None, reason="jsonschema library not installed")
    def test_jsonschema_validator_rejects_when_condition_matches(
        self, conditional_contract
    ):
        """A record with id_type='lei' AND a malformed party_id must
        be REJECTED."""
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(conditional_contract)
        record = {"id_type": "lei", "party_id": "BAD-LEI-FORMAT"}
        with pytest.raises(js.ValidationError):
            js.validate(instance=record, schema=schema)

    @pytest.mark.skipif(js is None, reason="jsonschema library not installed")
    def test_jsonschema_validator_accepts_when_condition_matches_and_value_passes(
        self, conditional_contract
    ):
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(conditional_contract)
        record = {"id_type": "lei", "party_id": "529900T8BM49AURSDO55"}
        js.validate(instance=record, schema=schema)


class TestNotValueConditional:
    def test_not_value_emits_if_then_else(self, not_value_contract):
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(not_value_contract)
        all_of = schema.get("allOf", [])
        assert len(all_of) == 1, all_of
        block = all_of[0]
        # not_value uses if/then-empty/else-with-constraint shape
        # so the "field is absent" case doesn't accidentally apply
        # the constraint.
        assert "if" in block and "else" in block, block
        # else branch carries the constraint.
        else_props = block["else"].get("properties", {})
        assert "customer_name" in else_props
        assert else_props["customer_name"].get("pattern") == r"^[A-Za-z ]+$"

    @pytest.mark.skipif(js is None, reason="jsonschema library not installed")
    def test_not_value_record_with_internal_region_accepts(self, not_value_contract):
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(not_value_contract)
        # INTERNAL region: constraint does NOT apply, special chars OK.
        record = {"region": "INTERNAL", "customer_name": "AcmeCo!@#"}
        js.validate(instance=record, schema=schema)

    @pytest.mark.skipif(js is None, reason="jsonschema library not installed")
    def test_not_value_record_with_other_region_rejects_special_chars(
        self, not_value_contract
    ):
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(not_value_contract)
        record = {"region": "EU", "customer_name": "AcmeCo!@#"}
        with pytest.raises(js.ValidationError):
            js.validate(instance=record, schema=schema)


class TestNoAllOfWhenNoConditionalRules:
    """Guard: contracts with only unconditional rules must NOT emit an
    `allOf` key at all (not even an empty list). Empty allOf is valid
    JSON Schema but trips assertions in consumers doing
    `'allOf' in schema` checks."""

    def test_unconditional_only_emits_no_allof(self, unconditional_contract):
        from opendqv.core.jsonschema import contract_to_jsonschema
        schema = contract_to_jsonschema(unconditional_contract)
        assert "allOf" not in schema, (
            f"v2.3.23 P1-4: contract with no conditional rules must "
            f"NOT emit an `allOf` key (additive only when used). "
            f"Got keys: {list(schema.keys())}"
        )
        # Regression: pattern goes on email property as before.
        assert schema["properties"]["email"].get("pattern") is not None
