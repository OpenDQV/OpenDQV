"""Shapes refused when a contract is submitted (3.0.5, managed-engine parity).

Each shape below used to load and then fail every record or never fire — the
silent class. The managed engine refuses them at create (422
``contract_rule_invalid``); Core refuses them on every way a contract is
*submitted*: MCP ``create_contract_draft``, the REST rule add/update routes,
``/import/*``, ``opendqv fork`` and ``opendqv lint`` (as errors).

Content already **stored** still loads, with a warning, and keeps its old
reading: a refusal at load would delist a contract at boot. That is the
opposite of 2.8.0's unknown-rule-type decision (refused at load), on purpose —
these shapes have a defined, if useless, old reading; an unknown type has none.
"""
from __future__ import annotations

from opendqv.core.validator import (
    CHECKSUM_ALGORITHMS,
    _GO_DATE_LAYOUT,
    _GO_RFC3339_LAYOUT,
    _LAYOUT_DIRECTIVES,
    _human_to_strptime,
    _ws_strip,
)

# word forms and the symbol aliases the model normalises; no other spellings
COMPARE_OPS = frozenset({"gt", "lt", "gte", "lte", "eq", "neq", "same_date",
                         ">", "<", ">=", "<=", "=", "!="})
_NUMERIC_BOUND_KEYS = ("min", "max", "min_value", "max_value", "min_length", "max_length", "min_age", "max_age")


def _format_problems(fmt) -> list[str]:
    if not isinstance(fmt, str):
        return [f"format must be a string, got {type(fmt).__name__}"]
    if fmt == _GO_DATE_LAYOUT:
        return [f'format "{fmt}" is a Go layout, not a date format — write "%Y-%m-%d" (or YYYY-MM-DD)']
    if fmt == _GO_RFC3339_LAYOUT:
        return [f'format "{fmt}" is a Go layout, not a date format — omit format to read ISO 8601 '
                f'(a zone is then optional), or write "%Y-%m-%dT%H:%M:%S" for a fixed shape']
    if fmt != _ws_strip(fmt):
        return ["format has white space around it — a layout character is matched exactly, so remove it"]
    out: list[str] = []
    spelled = _human_to_strptime(fmt)
    directives = 0
    i = 0
    while i < len(spelled):
        if spelled[i] != "%":
            i += 1
            continue
        d = spelled[i + 1] if i + 1 < len(spelled) else ""
        if d == "%":
            pass
        elif d not in _LAYOUT_DIRECTIVES:
            out.append(f'format directive "%{d}" is not supported — use %Y %y %m %d %H %M %S %f '
                       f"(or YYYY YY MM DD HH MM SS)")
        elif d == "f" and (i == 0 or spelled[i - 1] not in ".,"):
            out.append('format directive "%f" must follow "." or "," (e.g. %S.%f)')
        else:
            directives += 1
        i += 2
    if directives == 0 and not out:
        out.append(f'format "{fmt}" has no date directive (directives are case-sensitive: '
                   f"YYYY-MM-DD or %Y-%m-%d) — every value would fail")
    return out


def _condition_problems(cond, label: str) -> list[str]:
    if not isinstance(cond, dict):
        return []
    out = []
    if "value" in cond and "not_value" in cond:
        out.append(f"{label} has both value and not_value — use one")
    for k, replacement in (("value", "present: false"), ("not_value", "present: true")):
        if k in cond and cond[k] is None:
            out.append(f"{label} {k}: null never matches — use {replacement}")
    return out


def _trigger_problems(trig, key: str) -> list[str]:
    if not isinstance(trig, dict):
        return []
    out = []
    if "equals" in trig:
        out.append(f"{key} uses equals: — write value:")
    if "value" not in trig:
        if "equals" not in trig:
            out.append(f"{key} has no value: — name the trigger value")
    elif trig["value"] is None:
        out.append(f"{key} value: null never matches — name the trigger value")
    return out


def rule_submission_problems(raw: dict) -> list[str]:
    """What the managed engine refuses about one rule document (a YAML/JSON
    dict, before the model normalises it). Empty when the rule is accepted."""
    if not isinstance(raw, dict):
        return []
    rtype = raw.get("type")
    out: list[str] = []
    op = raw.get("compare_op")
    if op is not None and (not isinstance(op, str) or op not in COMPARE_OPS):
        out.append(f'compare_op "{op}" is not one of gt lt gte lte eq neq same_date (or > < >= <= = !=)')
    out += _condition_problems(raw.get("condition"), "condition")
    if raw.get("negate") is True and rtype != "regex":
        out.append("negate: true is for regex only — use forbidden_values to refuse listed values")
    for key in ("required_if", "forbidden_if"):
        out += _trigger_problems(raw.get(key), key)
    if "format" in raw and raw["format"] is not None:
        out += _format_problems(raw["format"])
    for k in _NUMERIC_BOUND_KEYS:
        v = raw.get(k)
        if isinstance(v, (str, bool)):
            out.append(f'{k} must be a number, got {type(v).__name__} {v!r}')
    has_min = raw.get("min", raw.get("min_value")) is not None
    has_max = raw.get("max", raw.get("max_value")) is not None
    if rtype == "range" and not (has_min and has_max):
        out.append("range needs both min and max — use min or max for a single bound")
    if rtype == "regex" and not raw.get("pattern"):
        out.append("regex needs a pattern")
    if rtype == "allowed_values" and not raw.get("allowed_values"):
        out.append("allowed_values needs at least one value")
    if rtype == "lookup" and not raw.get("lookup_file"):
        out.append("lookup needs a lookup_file")
    if rtype == "checksum":
        alg = raw.get("checksum_algorithm")
        if not alg:
            out.append("checksum needs a checksum_algorithm")
        elif alg not in CHECKSUM_ALGORITHMS:
            out.append(f'checksum_algorithm "{alg}" is not supported (names are lower case): '
                       f"{', '.join(CHECKSUM_ALGORITHMS)}")
    return out


def contract_submission_problems(doc: dict) -> list[str]:
    """Every problem in a contract document's rules, each prefixed with the
    rule's name. Only the list form of ``rules:`` is checked (the field-keyed
    onboarding form has its own vocabulary)."""
    rules = (doc or {}).get("rules")
    if not isinstance(rules, list):
        return []
    out = []
    for i, r in enumerate(rules):
        name = r.get("name", f"#{i}") if isinstance(r, dict) else f"#{i}"
        out += [f'rule "{name}": {p}' for p in rule_submission_problems(r)]
    return out


def refuse_if_problems(doc_or_rule: dict, *, rule: bool = False) -> None:
    """Raise ValueError naming every problem (the submission paths' gate)."""
    problems = rule_submission_problems(doc_or_rule) if rule else contract_submission_problems(doc_or_rule)
    if problems:
        raise ValueError("contract_rule_invalid: " + "; ".join(problems))


__all__ = ["COMPARE_OPS", "rule_submission_problems", "contract_submission_problems", "refuse_if_problems"]
