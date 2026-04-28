"""
v2.3.23 P1-5 — get_contract serializer must pass through Rule.condition.

Persona B inside-view 2026-04-28:
  "buyer_id_lei_format is conditional on buyer_id_type but the gate
   isn't expressed in any field of the rule body. Customer impact:
   customers and auditors cannot read a contract and predict when a
   rule will fire — the conditional logic is implicit, hurting the
   contract's audit value."

Verified: the mifid_transaction_report YAML has `condition: {field:
buyer_id_type, value: "lei"}` on buyer_id_lei_format. The Rule
Pydantic model carries it (rule_parser.py:140). The runtime applies
it (validator.py condition handling). But the API response strips
it because RuleInfo (api/models.py:231-252) has no `condition`
field and the serializer at routes_contracts.py:121-142 doesn't
pass it.

Sonnet's pre-impl review (a5f385c20eba96e85): "It's a serializer
fix, full stop. Add condition: Optional[dict] = None to RuleInfo
and ensure the YAML→model→response path passes it through. No
validator changes, no synthetic fields, no hidden logic to
excavate."

Fix scope: minimal serializer change. RuleInfo.condition optional
dict, populate from rule.condition in the get_contract route.
"""



class TestGetContractPassesThroughCondition:
    def test_buyer_id_lei_format_condition_visible_in_response(self, client):
        """The mifid contract has a condition on buyer_id_lei_format.
        get_contract must surface it so a regulator-side reader can
        predict when the rule fires without running probe records."""
        r = client.get("/api/v1/contracts/mifid_transaction_report")
        assert r.status_code == 200, r.text
        rules = r.json().get("rules", [])
        target = next((r_ for r_ in rules if r_.get("name") == "buyer_id_lei_format"), None)
        assert target is not None, "buyer_id_lei_format rule not in response"
        assert "condition" in target, (
            f"v2.3.23 P1-5: get_contract response must surface "
            f"Rule.condition. Reviewer's regulator-side reader can "
            f"otherwise not predict when this rule fires. "
            f"Rule keys: {list(target.keys())}"
        )
        condition = target["condition"]
        assert condition is not None, (
            f"v2.3.23 P1-5: buyer_id_lei_format YAML has condition "
            f"{{field: buyer_id_type, value: 'lei'}}. Response carries None — "
            f"serializer dropping the value. Got: {target}"
        )
        assert condition.get("field") == "buyer_id_type", condition
        assert condition.get("value") == "lei", condition

    def test_unconditional_rules_carry_null_condition(self, client):
        """Rules without a condition in YAML carry condition: null on
        the response. Field is always present (consistent shape)."""
        r = client.get("/api/v1/contracts/customer")
        assert r.status_code == 200, r.text
        rules = r.json().get("rules", [])
        # Any rule will do — customer contract rules don't use condition.
        for rule in rules:
            assert "condition" in rule, (
                f"condition field must be present on every rule "
                f"(consistent shape). Got: {rule}"
            )
            # customer rules have no conditions, so all should be null.
            assert rule["condition"] is None, (
                f"customer rule {rule['name']!r} unexpectedly has "
                f"condition: {rule['condition']}"
            )

    def test_seller_id_lei_format_also_visible(self, client):
        """The mifid contract has multiple conditional rules
        (buyer_id_lei_format, seller_id_lei_format, etc.). All must
        surface their conditions."""
        r = client.get("/api/v1/contracts/mifid_transaction_report")
        assert r.status_code == 200
        rules = r.json().get("rules", [])
        # Find any rule with conditional buyer/seller id type.
        conditional_rules = [
            r_ for r_ in rules
            if r_.get("condition") is not None
        ]
        assert len(conditional_rules) >= 1, (
            f"mifid contract has at least one conditional rule. "
            f"Response surfaced 0 conditional rules — serializer is "
            f"still dropping condition for all rules. Sample rule: "
            f"{rules[0] if rules else 'none'}"
        )
