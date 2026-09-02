"""CRT180 — `not_empty_string`: presence + JSON-string type guard.

The contract format must mean the same thing on every engine that runs it.
`not_empty` accepts any present, non-blank value (0, false and [] are
stringified and pass); `not_empty_string` is the type-guarded variant a
form/API boundary needs. These tests pin single-record ↔ batch parity and
every surface that has to know a rule type exists.
"""
import yaml

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record


def _rule(**over):
    base = {"name": "email_required", "type": "not_empty_string", "field": "email",
            "severity": "error", "error_message": "Email is required"}
    base.update(over)
    return Rule(**base)


class TestSingleRecord:
    def test_non_empty_string_passes(self):
        assert validate_record({"email": "a@b.co"}, [_rule()])["valid"] is True

    def test_empty_whitespace_none_missing_fail_with_rule_code(self):
        for rec in ({"email": ""}, {"email": "   "}, {"email": None}, {}):
            res = validate_record(rec, [_rule()])
            assert res["valid"] is False, rec
            assert res["errors"][0]["error_code"].startswith("OPENDQV_NOT_EMPTY_STRING_")
            assert res["errors"][0]["message"] == "Email is required"

    def test_non_string_values_are_type_mismatch_not_coerced(self):
        for val in (0, 1, 1.5, False, True, [], ["x"], {}, {"k": "v"}):
            res = validate_record({"email": val}, [_rule()])
            assert res["valid"] is False, val
            err = res["errors"][0]
            assert err["error_code"] == "OPENDQV_TYPE_MISMATCH", val
            assert "must be a string" in err["message"]

    def test_not_empty_still_coerces_for_contrast(self):
        res = validate_record({"email": 0}, [_rule(type="not_empty", name="e")])
        assert res["valid"] is True


class TestBatchParity:
    def test_batch_matches_single_record(self):
        records = [
            {"email": "a@b.co"}, {"email": ""}, {"email": "   "}, {"email": None},
            {"email": 0}, {"email": False}, {"email": ["x"]},
        ]
        rules = [_rule()]
        single = [validate_record(r, rules) for r in records]
        batch = validate_batch(records, rules)
        for s, b in zip(single, batch["results"]):
            assert s["valid"] == b["valid"]
            s_codes = sorted(e["error_code"] for e in s["errors"])
            b_codes = sorted(e["error_code"] for e in b["errors"])
            assert s_codes == b_codes
        assert batch["summary"]["failed"] == 6


class TestSurfaces:
    CONTRACT_YAML = """
contract:
  name: crt180_probe
  version: "1.0"
  owner: qa
  owner_email: qa@example.com
  rules:
    - name: email_required
      type: not_empty_string
      field: email
      severity: error
      error_message: Email is required
"""

    def test_linter_knows_the_type(self):
        from opendqv.core.linter import lint_contract_yaml
        result = lint_contract_yaml(self.CONTRACT_YAML, "crt180_probe")
        assert not [i for i in result.issues if i.code == "UNKNOWN_RULE_TYPE"]

    def test_jsonschema_projection(self):
        from opendqv.core.contracts import DataContract
        from opendqv.core.jsonschema import contract_to_jsonschema
        dc = DataContract(name="crt180_probe", rules=[_rule()])
        schema = contract_to_jsonschema(dc)
        assert "email" in schema["required"]
        assert schema["properties"]["email"]["type"] == "string"
        assert schema["properties"]["email"]["minLength"] == 1

    def test_explainer(self):
        from opendqv.core.explainer import explain_rule
        out = explain_rule(_rule())
        assert out["rule_type"] == "not_empty_string"
        assert 0 in out["invalid_examples"]

    def test_code_generators_emit_a_type_guard(self):
        from opendqv.core.code_generator import generate_code
        rules = [_rule()]
        js = generate_code(rules, "js")
        assert "typeof row['email'] !== 'string'" in js
        assert "typeof row['email'] !== 'string'" in generate_code(rules, "snowflake")
        assert "typeof row['email'] !== 'string'" in generate_code(rules, "bigquery")
        assert "instanceof String" in generate_code(rules, "salesforce")
        assert "typeof(email) != 'string'" in generate_code(rules, "spark")

    def test_odcs_projection_and_round_trip(self):
        from opendqv.core.importers import odcs
        doc = odcs.export_odcs("crt180_probe", [_rule()], version="1.0", status="active")
        prop = doc["schema"][0]["properties"][0]
        assert prop["name"] == "email"
        assert prop["required"] is True
        assert prop["logicalType"] == "string"
        custom = [q for q in prop["quality"] if q.get("type") == "custom"]
        assert custom and custom[0]["implementation"]["type"] == "not_empty_string"
        back = odcs.import_odcs(doc)
        rules = back["contract"]["rules"]
        assert [r["type"] for r in rules] == ["not_empty_string"]
        assert rules[0]["error_message"] == "Email is required"

    def test_yaml_parses(self):
        data = yaml.safe_load(self.CONTRACT_YAML)
        rules = [Rule(**r) for r in data["contract"]["rules"]]
        assert rules[0].type == "not_empty_string"
