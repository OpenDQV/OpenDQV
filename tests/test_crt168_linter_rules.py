"""CRT168 PR-C — linter rule tests.

Two new linter checks landed alongside the audit-credibility sweep:

- OWNER_EMAIL_MISSING / OWNER_EMAIL_INVALID — every contract must name a
  contact, otherwise the audit trail is anonymous when a regulator follows up.
- UNIQUE_RULE_MISSING_SCOPE_NOTE — `unique` rules must qualify the scope in
  their error_message. The engine only de-duplicates within the input batch;
  promising bare "must be unique" overstates coverage.

Also confirms all 41 bundled contracts lint clean.
"""

from pathlib import Path

import pytest

from opendqv.core.linter import lint_contract_file, lint_contract_yaml


_BASE_YAML = """
contract:
  name: t
  owner: T Team
  owner_email: t@example.com
  status: active
  rules:
    - name: r
      type: not_empty
      field: x
      error_message: x is required
"""


def _codes(result) -> set[str]:
    return {i.code for i in result.issues}


class TestOwnerEmailLint:
    def test_missing_owner_email_is_warning(self):
        yaml_str = _BASE_YAML.replace("  owner_email: t@example.com\n", "")
        result = lint_contract_yaml(yaml_str)
        codes = _codes(result)
        assert "OWNER_EMAIL_MISSING" in codes
        assert all(i.severity != "error" for i in result.issues)

    def test_present_owner_email_is_silent(self):
        result = lint_contract_yaml(_BASE_YAML)
        assert "OWNER_EMAIL_MISSING" not in _codes(result)
        assert "OWNER_EMAIL_INVALID" not in _codes(result)

    def test_garbage_owner_email_flags_invalid(self):
        yaml_str = _BASE_YAML.replace("t@example.com", "not-an-email")
        result = lint_contract_yaml(yaml_str)
        assert "OWNER_EMAIL_INVALID" in _codes(result)


_UNIQUE_YAML = """
contract:
  name: u
  owner: U Team
  owner_email: u@example.com
  status: active
  rules:
    - name: unique_id
      type: unique
      field: id
      error_message: {msg}
"""


class TestUniqueScopeNoteLint:
    def test_bare_unique_message_warns(self):
        yaml_str = _UNIQUE_YAML.format(msg='"Duplicate detected."')
        result = lint_contract_yaml(yaml_str)
        assert "UNIQUE_RULE_MISSING_SCOPE_NOTE" in _codes(result)

    def test_within_batch_message_passes(self):
        yaml_str = _UNIQUE_YAML.format(msg='"Duplicate detected within this batch."')
        result = lint_contract_yaml(yaml_str)
        assert "UNIQUE_RULE_MISSING_SCOPE_NOTE" not in _codes(result)

    def test_within_file_message_passes(self):
        yaml_str = _UNIQUE_YAML.format(msg='"Duplicate within this file."')
        result = lint_contract_yaml(yaml_str)
        assert "UNIQUE_RULE_MISSING_SCOPE_NOTE" not in _codes(result)

    def test_dataset_qualifier_passes(self):
        yaml_str = _UNIQUE_YAML.format(msg='"Duplicate id in this dataset."')
        result = lint_contract_yaml(yaml_str)
        assert "UNIQUE_RULE_MISSING_SCOPE_NOTE" not in _codes(result)

    def test_empty_error_message_warns(self):
        yaml_str = _UNIQUE_YAML.format(msg='""')
        result = lint_contract_yaml(yaml_str)
        assert "UNIQUE_RULE_MISSING_SCOPE_NOTE" in _codes(result)

    def test_unique_warning_does_not_fire_on_non_unique_rule(self):
        result = lint_contract_yaml(_BASE_YAML)
        assert "UNIQUE_RULE_MISSING_SCOPE_NOTE" not in _codes(result)


class TestBundledContractsLintClean:
    """Audit-credibility safety net — every shipped contract must pass."""

    @pytest.mark.parametrize("path", sorted(Path("opendqv/contracts").glob("*.yaml")))
    def test_each_bundled_contract_lints_clean(self, path):
        result = lint_contract_file(str(path))
        assert result.passed, (
            f"{path.name} has lint errors: "
            f"{[i.to_dict() for i in result.errors]}"
        )
        assert not result.warnings, (
            f"{path.name} has lint warnings: "
            f"{[i.to_dict() for i in result.warnings]}"
        )
