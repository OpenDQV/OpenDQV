"""
Contract linter — static analysis of contract YAML before deployment.

Catches logical errors and missing required fields that the runtime validator
either silently skips or only surfaces at the first failing record.

Checks performed:
  - Duplicate rule names within a contract
  - Unknown rule type (not in the supported set)
  - Range: min_value > max_value
  - Length: min_length > max_length
  - Age: min_age > max_age
  - Regex: pattern that does not compile
  - compare: missing compare_to or compare_op; invalid compare_op
  - cross_field_range: missing cross_min_field or cross_max_field
  - field_sum: missing sum_fields or sum_equals
  - geospatial_bounds: missing geo_lon_field, geo_min_lat, geo_max_lat,
                       geo_min_lon, or geo_max_lon
  - ratio_check: missing ratio_numerator or ratio_denominator
  - date_diff: missing date_diff_field
  - checksum: missing checksum_algorithm; unknown algorithm
  - lookup: missing lookup_file
  - allowed_values: empty allowed_values list
  - required_if: missing 'field' or 'value' keys in required_if dict
  - forbidden_if: missing 'field' or 'value' keys in forbidden_if dict
  - conditional_value: missing must_equal or condition
"""

import re
from dataclasses import dataclass, field
from typing import Optional
import yaml


# ── Supported rule types ──────────────────────────────────────────────────────

_KNOWN_RULE_TYPES = frozenset({
    "not_empty",
    "regex",
    "min",
    "max",
    "range",
    "min_length",
    "max_length",
    "date_format",
    "unique",
    "min_age",
    "max_age",
    "compare",
    "required_if",
    "lookup",
    "checksum",
    "cross_field_range",
    "field_sum",
    "forbidden_if",
    "conditional_value",
    "date_diff",
    "ratio_check",
    "geospatial_bounds",
    "allowed_values",
    "age_match",
})

_KNOWN_CHECKSUM_ALGORITHMS = frozenset({
    "mod10_gs1",
    "iban_mod97",
    "isin_mod11",
    "lei_mod97",
    "vin_mod11",
    "isrc_luhn",
    "cpf_mod11",
    "nhs_mod11",
})

_KNOWN_COMPARE_OPS = frozenset({
    "gt", "lt", "gte", "lte", "eq", "neq",
    ">", "<", ">=", "<=", "=", "!=",
})


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class LintIssue:
    """A single linting finding."""
    severity: str          # "error" | "warning"
    rule_name: Optional[str]  # None for contract-level issues
    code: str              # short identifier, e.g. "DUPLICATE_RULE_NAME"
    message: str

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "rule_name": self.rule_name,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class LintResult:
    """Aggregated linting output for one contract."""
    contract_name: str
    issues: list = field(default_factory=list)

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        return {
            "contract_name": self.contract_name,
            "passed": self.passed,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
        }


# ── Core linting logic ────────────────────────────────────────────────────────

def lint_contract_yaml(yaml_str: str, contract_name: str = "") -> LintResult:
    """
    Lint a contract YAML string.

    Works at the raw dict level (pre-Pydantic) so it can flag issues that
    would be silently swallowed or raise confusing errors at parse time.
    Returns a LintResult with all issues found.
    """
    result = LintResult(contract_name=contract_name)

    try:
        data = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError as e:
        result.issues.append(LintIssue(
            severity="error",
            rule_name=None,
            code="YAML_PARSE_ERROR",
            message=f"YAML parse error: {e}",
        ))
        return result

    if not isinstance(data, dict):
        result.issues.append(LintIssue(
            severity="error",
            rule_name=None,
            code="INVALID_STRUCTURE",
            message="Contract YAML must be a mapping at the top level.",
        ))
        return result

    # Use contract name from YAML if not provided
    contract_node = data.get("contract", {})
    if isinstance(contract_node, dict) and not contract_name:
        contract_name = contract_node.get("name", "")
        result.contract_name = contract_name

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        result.issues.append(LintIssue(
            severity="error",
            rule_name=None,
            code="INVALID_RULES_STRUCTURE",
            message="'rules' must be a list.",
        ))
        return result

    # ── Duplicate rule names ──────────────────────────────────────────────────
    seen_names: dict[str, int] = {}
    for i, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            result.issues.append(LintIssue(
                severity="error",
                rule_name=None,
                code="INVALID_RULE_ENTRY",
                message=f"Rule at index {i} is not a mapping.",
            ))
            continue
        name = raw.get("name", f"<unnamed-{i}>")
        if name in seen_names:
            result.issues.append(LintIssue(
                severity="error",
                rule_name=name,
                code="DUPLICATE_RULE_NAME",
                message=f"Rule name '{name}' is used more than once (first at index {seen_names[name]}, again at index {i}).",
            ))
        else:
            seen_names[name] = i

    # ── Per-rule checks ───────────────────────────────────────────────────────
    for i, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            continue  # already flagged above

        name = raw.get("name", f"<unnamed-{i}>")
        rule_type = raw.get("type", "")

        def err(code, msg):
            result.issues.append(LintIssue(severity="error", rule_name=name, code=code, message=msg))

        def warn(code, msg):
            result.issues.append(LintIssue(severity="warning", rule_name=name, code=code, message=msg))

        # Unknown rule type
        if rule_type not in _KNOWN_RULE_TYPES:
            err("UNKNOWN_RULE_TYPE",
                f"Unknown rule type '{rule_type}'. Known types: {sorted(_KNOWN_RULE_TYPES)}")
            # Don't run further checks — they'd all be noise
            continue

        # ── Range / bound checks ──────────────────────────────────────────────
        min_val = raw.get("min") if raw.get("min") is not None else raw.get("min_value")
        max_val = raw.get("max") if raw.get("max") is not None else raw.get("max_value")
        if min_val is not None and max_val is not None:
            try:
                if float(min_val) > float(max_val):
                    err("RANGE_MIN_GT_MAX",
                        f"min ({min_val}) is greater than max ({max_val}) — no value can ever pass this rule.")
            except (TypeError, ValueError):
                pass  # non-numeric; runtime will handle

        min_len = raw.get("min_length")
        max_len = raw.get("max_length")
        if min_len is not None and max_len is not None:
            try:
                if int(min_len) > int(max_len):
                    err("LENGTH_MIN_GT_MAX",
                        f"min_length ({min_len}) is greater than max_length ({max_len}).")
            except (TypeError, ValueError):
                pass

        min_age = raw.get("min_age")
        max_age = raw.get("max_age")
        if min_age is not None and max_age is not None:
            try:
                if int(min_age) > int(max_age):
                    err("AGE_MIN_GT_MAX",
                        f"min_age ({min_age}) is greater than max_age ({max_age}).")
            except (TypeError, ValueError):
                pass

        # ── Regex: pattern compiles ───────────────────────────────────────────
        if rule_type == "regex":
            pattern = raw.get("pattern")
            if not pattern:
                err("REGEX_MISSING_PATTERN",
                    "regex rule has no 'pattern' field — every record will fail.")
            else:
                _BUILTIN_PREFIX = "builtin:"
                if not pattern.startswith(_BUILTIN_PREFIX):
                    try:
                        re.compile(pattern)
                    except re.error as e:
                        err("REGEX_INVALID_PATTERN",
                            f"pattern '{pattern}' does not compile: {e}")

        # ── compare ───────────────────────────────────────────────────────────
        if rule_type == "compare":
            compare_to = raw.get("compare_to")
            compare_op = raw.get("compare_op")
            if not compare_to:
                err("COMPARE_MISSING_COMPARE_TO",
                    "compare rule requires 'compare_to' (field name or 'today'/'now').")
            if not compare_op:
                err("COMPARE_MISSING_COMPARE_OP",
                    f"compare rule requires 'compare_op'. Valid ops: {sorted(_KNOWN_COMPARE_OPS)}")
            elif compare_op not in _KNOWN_COMPARE_OPS:
                err("COMPARE_INVALID_OP",
                    f"Unknown compare_op '{compare_op}'. Valid ops: {sorted(_KNOWN_COMPARE_OPS)}")

        # ── cross_field_range ─────────────────────────────────────────────────
        if rule_type == "cross_field_range":
            if not raw.get("cross_min_field"):
                err("CROSS_FIELD_RANGE_MISSING_MIN",
                    "cross_field_range rule requires 'cross_min_field'.")
            if not raw.get("cross_max_field"):
                err("CROSS_FIELD_RANGE_MISSING_MAX",
                    "cross_field_range rule requires 'cross_max_field'.")

        # ── field_sum ─────────────────────────────────────────────────────────
        if rule_type == "field_sum":
            if not raw.get("sum_fields"):
                err("FIELD_SUM_MISSING_SUM_FIELDS",
                    "field_sum rule requires 'sum_fields' (list of field names).")
            if raw.get("sum_equals") is None:
                err("FIELD_SUM_MISSING_SUM_EQUALS",
                    "field_sum rule requires 'sum_equals' (target value).")

        # ── geospatial_bounds ─────────────────────────────────────────────────
        if rule_type == "geospatial_bounds":
            required_geo = ["geo_lon_field", "geo_min_lat", "geo_max_lat", "geo_min_lon", "geo_max_lon"]
            for req in required_geo:
                if raw.get(req) is None:
                    err("GEOSPATIAL_MISSING_FIELD",
                        f"geospatial_bounds rule requires '{req}'.")

        # ── ratio_check ───────────────────────────────────────────────────────
        if rule_type == "ratio_check":
            if not raw.get("ratio_numerator"):
                err("RATIO_MISSING_NUMERATOR",
                    "ratio_check rule requires 'ratio_numerator'.")
            if not raw.get("ratio_denominator"):
                err("RATIO_MISSING_DENOMINATOR",
                    "ratio_check rule requires 'ratio_denominator'.")

        # ── date_diff ─────────────────────────────────────────────────────────
        if rule_type == "date_diff":
            if not raw.get("date_diff_field"):
                err("DATE_DIFF_MISSING_FIELD",
                    "date_diff rule requires 'date_diff_field'.")

        # ── checksum ─────────────────────────────────────────────────────────
        if rule_type == "checksum":
            algo = raw.get("checksum_algorithm")
            if not algo:
                err("CHECKSUM_MISSING_ALGORITHM",
                    f"checksum rule requires 'checksum_algorithm'. "
                    f"Valid algorithms: {sorted(_KNOWN_CHECKSUM_ALGORITHMS)}")
            elif algo not in _KNOWN_CHECKSUM_ALGORITHMS:
                err("CHECKSUM_UNKNOWN_ALGORITHM",
                    f"Unknown checksum_algorithm '{algo}'. "
                    f"Valid algorithms: {sorted(_KNOWN_CHECKSUM_ALGORITHMS)}")

        # ── lookup ────────────────────────────────────────────────────────────
        if rule_type == "lookup":
            if not raw.get("lookup_file"):
                err("LOOKUP_MISSING_FILE",
                    "lookup rule requires 'lookup_file' (path or URL).")

        # ── allowed_values ────────────────────────────────────────────────────
        if rule_type == "allowed_values":
            av = raw.get("allowed_values")
            if not av:
                err("ALLOWED_VALUES_EMPTY",
                    "allowed_values rule requires a non-empty 'allowed_values' list.")

        # ── required_if ───────────────────────────────────────────────────────
        if rule_type == "required_if":
            ri = raw.get("required_if")
            if not isinstance(ri, dict):
                err("REQUIRED_IF_INVALID",
                    "required_if rule requires 'required_if: {field: ..., value: ...}'.")
            else:
                if "field" not in ri:
                    err("REQUIRED_IF_MISSING_FIELD",
                        "required_if dict is missing 'field' key.")
                if "value" not in ri:
                    err("REQUIRED_IF_MISSING_VALUE",
                        "required_if dict is missing 'value' key.")

        # ── forbidden_if ──────────────────────────────────────────────────────
        if rule_type == "forbidden_if":
            fi = raw.get("forbidden_if")
            if not isinstance(fi, dict):
                err("FORBIDDEN_IF_INVALID",
                    "forbidden_if rule requires 'forbidden_if: {field: ..., value: ...}'.")
            else:
                if "field" not in fi:
                    err("FORBIDDEN_IF_MISSING_FIELD",
                        "forbidden_if dict is missing 'field' key.")
                if "value" not in fi:
                    err("FORBIDDEN_IF_MISSING_VALUE",
                        "forbidden_if dict is missing 'value' key.")

        # ── conditional_value ─────────────────────────────────────────────────
        if rule_type == "conditional_value":
            if raw.get("must_equal") is None:
                err("CONDITIONAL_VALUE_MISSING_MUST_EQUAL",
                    "conditional_value rule requires 'must_equal'.")
            cond = raw.get("condition")
            if not isinstance(cond, dict):
                err("CONDITIONAL_VALUE_MISSING_CONDITION",
                    "conditional_value rule requires 'condition: {field: ..., value: ...}'.")

    return result


def lint_contract_file(path: str) -> LintResult:
    """Lint a contract YAML file by path."""
    from pathlib import Path as _Path
    p = _Path(path)
    contract_name = p.stem
    try:
        yaml_str = p.read_text(encoding="utf-8")
    except OSError as e:
        result = LintResult(contract_name=contract_name)
        result.issues.append(LintIssue(
            severity="error",
            rule_name=None,
            code="FILE_READ_ERROR",
            message=f"Cannot read file: {e}",
        ))
        return result
    return lint_contract_yaml(yaml_str, contract_name=contract_name)
