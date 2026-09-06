"""
Open Data Contract Standard (ODCS) v3.1.0 importer and exporter.

Reference: https://bitol-io.github.io/open-data-contract-standard/latest/
Schema:    schema/odcs-json-schema-v3.1.0.json in the bitol-io repository.

This module provides bidirectional conversion:
  import_odcs(contract_data)     → OpenDQV contract dict (+ skipped_checks)
  odcs_to_yaml(contract_data)    → (contract_name, OpenDQV YAML string)
  export_odcs(name, rules, ...)  → ODCS 3.1.0 dict
  contract_to_odcs_yaml(...)     → ODCS 3.1.0 YAML string

ODCS 3.1.0 shape (the parts OpenDQV reads and writes):

  apiVersion: v3.1.0            # required; v3.0.x accepted on import
  kind: DataContract            # required
  id: <str>                     # required — OpenDQV uses the contract name
  name: <str>
  version: <str>                # required
  status: <str>                 # required — draft | proposed | active | deprecated | retired
  description: {purpose, usage, limitations}
  team: {name, members: [{username, role}]}
  customProperties: [{property, value}]
  schema:
    - name: <object>
      logicalType: object
      properties:
        - name: <field>
          logicalType: string | number | integer | date | ...
          required: <bool>
          unique: <bool>
          logicalTypeOptions:   # keys depend on logicalType (schema-enforced)
            pattern / minLength / maxLength / format      (string)
            minimum / maximum / exclusiveMinimum / ...    (number, integer)
            format / minimum / maximum                    (date)
          quality:
            - type: library     # metric: nullValues | duplicateValues | invalidValues | ...
              metric: <str>
              mustBe: <n>       # exactly one mustBe* operator
            - type: custom      # engine + implementation are required
              engine: opendqv
              implementation: {<OpenDQV rule dict>}

Export strategy (lossless + interoperable):
  * Every OpenDQV rule is emitted as a ``type: custom, engine: opendqv`` quality
    entry whose ``implementation`` is the rule itself. That is the authoritative
    form and round-trips exactly (severity, error_message, cross-field params).
  * Rules with severity ``error`` are ALSO projected onto the native ODCS
    fields other tools understand (``required``, ``unique``,
    ``logicalTypeOptions``). Warning-severity rules are never projected —
    a native ODCS constraint is a hard constraint to every other consumer.
  * A field has exactly one ``logicalType``; only rules compatible with it are
    projected natively (the schema forbids e.g. ``pattern`` on a number).

Import strategy:
  * If a property carries ``custom/opendqv`` entries, rules come from those and
    the native projections on that property are ignored (they are echoes).
  * Otherwise rules are derived from ``required``/``unique``/``logicalTypeOptions``
    and from record-level library metrics (``nullValues``/``duplicateValues``/
    ``invalidValues`` with ``mustBe: 0``). Everything else is dataset-level or
    not expressible and is reported in ``skipped_checks`` — never dropped silently.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from opendqv.core.rule_parser import _BUILTIN_PATTERNS, Rule, RULE_KEYS

ODCS_API_VERSION = "v3.1.0"
ODCS_ENGINE = "opendqv"
_SUPPORTED_API_VERSIONS = {"v3.0.0", "v3.0.1", "v3.0.2", "v3.1.0"}

# ---------------------------------------------------------------------------
# Status mapping  (OpenDQV lifecycle ⇄ ODCS status vocabulary)
# ---------------------------------------------------------------------------

_STATUS_TO_ODCS = {
    "draft": "draft",
    "review": "proposed",
    "active": "active",
    "archived": "deprecated",
}
_STATUS_FROM_ODCS = {
    "draft": "draft",
    "proposed": "review",
    "active": "active",
    "deprecated": "archived",
    "retired": "archived",
}

# ---------------------------------------------------------------------------
# Rule-type → ODCS dimension / logicalType inference
# ---------------------------------------------------------------------------

_DIMENSION: dict[str, str] = {
    "not_empty": "completeness",
    "not_empty_string": "completeness",
    "required_if": "completeness",
    "unique": "uniqueness",
    "regex": "conformity",
    "min_length": "conformity",
    "max_length": "conformity",
    "date_format": "conformity",
    "checksum": "conformity",
    "lookup": "conformity",
    "allowed_values": "conformity",
    "forbidden_values": "conformity",   # no ODCS construct for a negative set: custom/opendqv only, never validValues
    "min": "accuracy",
    "max": "accuracy",
    "range": "accuracy",
    "min_age": "accuracy",
    "max_age": "accuracy",
    "geospatial_bounds": "accuracy",
}
_DEFAULT_DIMENSION = "consistency"   # cross-field / conditional rules

# Precedence when rules on one field imply different logical types.
# Numeric wins over date wins over string so that min/max projections survive;
# a field that is both "date_format" and "min" is a modelling error upstream.
_NUMERIC_TYPES = {"min", "max", "range", "cross_field_range", "field_sum", "ratio_check", "geospatial_bounds"}
_DATE_TYPES = {"date_format", "min_age", "max_age", "age_match", "date_diff"}


def _infer_logical_type(rule_types: set[str]) -> str:
    if "not_empty_string" in rule_types:
        return "string"  # the type guard IS the assertion; it wins over numeric inference
    if rule_types & _NUMERIC_TYPES:
        return "number"
    if rule_types & _DATE_TYPES:
        return "date"
    return "string"


# Constructs outside RE2 / ECMA-262-portable regex: lookahead, lookbehind,
# atomic groups, backreferences. The ODCS reference implementation executes
# `pattern` with RE2 and a single lookahead aborts every check on the object,
# so such patterns stay custom-only (the OpenDQV engine still enforces them).
_NON_PORTABLE_RE = re.compile(r"\(\?[=!<>]|\\[1-9]")


def _portable_pattern(pattern: str) -> Optional[str]:
    expanded = _BUILTIN_PATTERNS.get(pattern, pattern)
    return None if _NON_PORTABLE_RE.search(expanded) else expanded


_TIME_MARKERS = ("%H", "%M", "%S", "%f", "HH", "mm", "ss")


def _has_time(fmt: Optional[str]) -> bool:
    return bool(fmt) and any(m in fmt for m in _TIME_MARKERS)


# ---------------------------------------------------------------------------
# Date-format conversion  (strftime / human ⇄ JDK DateTimeFormatter)
# ---------------------------------------------------------------------------

_STRFTIME_TO_JDK = {
    "%Y": "yyyy", "%y": "yy", "%m": "MM", "%d": "dd",
    "%H": "HH", "%M": "mm", "%S": "ss", "%f": "SSSSSS", "%%": "%",
}
_HUMAN_TO_JDK = {"YYYY": "yyyy", "YY": "yy", "DD": "dd", "HH": "HH", "SS": "ss"}


def _to_jdk_format(fmt: str) -> Optional[str]:
    """Convert an OpenDQV date format to a JDK pattern; None if not representable."""
    if not fmt:
        return None
    if "%" in fmt:
        out, i = [], 0
        while i < len(fmt):
            if fmt[i] == "%":
                code = fmt[i:i + 2]
                if code not in _STRFTIME_TO_JDK:
                    return None
                out.append(_STRFTIME_TO_JDK[code])
                i += 2
            elif fmt[i].isalpha():
                # Every A-Z/a-z is a reserved JDK pattern letter; literal text
                # (e.g. the ISO 8601 'T') must be single-quoted.
                j = i
                while j < len(fmt) and fmt[j].isalpha():
                    j += 1
                out.append(f"'{fmt[i:j]}'")
                i = j
            else:
                out.append(fmt[i])
                i += 1
        return "".join(out)
    # Human form: YYYY-MM-DD / YYYY-MM-DD HH:MM:SS — MM is minutes after HH.
    out, i, seen_h = [], 0, False
    while i < len(fmt):
        if fmt[i:i + 4] == "YYYY":
            out.append("yyyy")
            i += 4
        elif fmt[i:i + 2] in ("YY", "DD", "HH", "SS"):
            out.append(_HUMAN_TO_JDK[fmt[i:i + 2]])
            seen_h = seen_h or fmt[i:i + 2] == "HH"
            i += 2
        elif fmt[i:i + 2] == "MM":
            out.append("mm" if seen_h else "MM")
            i += 2
        else:
            out.append(fmt[i])
            i += 1
    return "".join(out)


_JDK_TOKENS = [("yyyy", "%Y"), ("yy", "%y"), ("MM", "%m"), ("dd", "%d"),
               ("HH", "%H"), ("mm", "%M"), ("ss", "%S"), ("SSSSSS", "%f")]


def _from_jdk_format(fmt: str) -> Optional[str]:
    """Convert a JDK DateTimeFormatter pattern to strftime; None if unsupported."""
    if not fmt:
        return None
    out, i = [], 0
    while i < len(fmt):
        if fmt[i] == "'":
            end = fmt.find("'", i + 1)
            if end < 0:
                return None   # unterminated literal
            literal = fmt[i + 1:end] or "'"   # '' is an escaped quote in JDK
            out.append(literal.replace("%", "%%"))
            i = end + 1
            continue
        for tok, code in _JDK_TOKENS:
            if fmt.startswith(tok, i):
                out.append(code)
                i += len(tok)
                break
        else:
            ch = fmt[i]
            if ch.isalpha():
                return None   # unknown pattern letter (e.g. 'a', 'z', 'E')
            out.append(ch)
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Rule dict hygiene
# ---------------------------------------------------------------------------

# Fields an ODCS import may never set. They carry federation authority, audit
# provenance, or outbound-credential intent and are stamped by the engine, not
# by whoever hands us a document (Sonnet pre-implementation finding #1).
_IMPORT_DENIED_FIELDS = frozenset({
    "inherited", "federation_tier", "provenance", "severity_floor",
    "lookup_auth_header",
})
_ENGINE_STAMPED_FIELDS = _IMPORT_DENIED_FIELDS
_ALLOWED_RULE_FIELDS = frozenset(RULE_KEYS) - _IMPORT_DENIED_FIELDS   # the same set Rule() accepts (2.9.0)
_ALIAS_TO_FIELD = {"min": "min_value", "max": "max_value"}

_NAME_RE = re.compile(r"[^a-z0-9_]+")


def _sanitise_name(raw: str) -> str:
    name = _NAME_RE.sub("_", str(raw).strip().lower()).strip("_")
    return name or "imported_contract"


def _rule_to_dict(rule: Any) -> dict:
    """Rule object or dict → plain dict with canonical field names, defaults dropped."""
    if isinstance(rule, Rule):
        # mode="json": enums (severity_floor etc.) become plain strings, so the
        # YAML never carries a Python-specific !!python/object tag.
        d = rule.model_dump(mode="json", exclude_none=True, exclude_defaults=True, by_alias=False)
        # exclude_defaults drops severity=error / description="" — keep what matters
        d["type"] = rule.type
        d["field"] = rule.field
        d["name"] = rule.name
        d["severity"] = rule.severity.value if hasattr(rule.severity, "value") else str(rule.severity)
        d["error_message"] = rule.error_message
    else:
        d = {}
        for k, v in dict(rule).items():
            if v is None:
                continue
            d[_ALIAS_TO_FIELD.get(k, k)] = v
        sev = d.get("severity", "error")
        d["severity"] = sev.value if hasattr(sev, "value") else str(sev)
    if d.get("inherited") is False:
        d.pop("inherited")
    # Engine-stamped federation / credential fields are node-local state, are
    # refused on import, and mean nothing to another system: never export them.
    for k in _ENGINE_STAMPED_FIELDS:
        d.pop(k, None)
    # Deterministic key order: identity first, then the rest alphabetically.
    head = ["name", "type", "field", "severity", "error_message", "description"]
    ordered: dict[str, Any] = OrderedDict()
    for k in head:
        if k in d and d[k] not in ("", None):
            ordered[k] = d[k]
    for k in sorted(d):
        if k not in ordered and k not in head:
            ordered[k] = d[k]
    return dict(ordered)


def _sanitise_rule_dict(field: str, impl: dict) -> dict:
    """Allowlist a caller-supplied rule dict and validate it through the Rule model."""
    if not isinstance(impl, dict):
        raise ValueError(f"{field}: custom/opendqv implementation must be an object")
    clean: dict[str, Any] = {}
    for k, v in impl.items():
        canon = _ALIAS_TO_FIELD.get(k, k)
        if canon in _IMPORT_DENIED_FIELDS:
            raise ValueError(f"{field}: field '{k}' may not be set by an ODCS import")
        if canon not in _ALLOWED_RULE_FIELDS:
            raise ValueError(f"{field}: unknown rule field '{k}'")
        clean[canon] = v
    clean.setdefault("field", field)
    clean.setdefault("name", f"{field}_{clean.get('type', 'rule')}")
    return _rule_to_dict(_validate_rule(field, clean))


def _validate_rule(field: str, d: dict) -> Rule:
    """Construct a Rule, converting any failure (schema or regex compile) to a clean ValueError."""
    try:
        return Rule(**d)
    except ValidationError as exc:
        # One line per error, no stack detail — this reaches API clients as 422.
        msgs = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"{field}: invalid rule — {msgs}") from None
    except Exception as exc:  # e.g. regex.error from pattern pre-compile
        raise ValueError(f"{field}: invalid rule — {type(exc).__name__}: {exc}") from None


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rule(field: str, rtype: str, severity: str = "error", message: str = "", **params) -> dict:
    d = {"name": f"{field}_{rtype}", "type": rtype, "field": field, "severity": severity,
         "error_message": message or f"{field}: {rtype} check failed"}
    d.update({k: v for k, v in params.items() if v is not None})
    return d


def _native_rules(field: str, prop: dict, skipped: list[str], notes: list[str]) -> list[dict]:
    """Rules from required / unique / logicalTypeOptions."""
    rules: list[dict] = []
    if prop.get("required") is True:
        rules.append(_rule(field, "not_empty", message=f"{field} is required"))
        notes.append(f"{field}.required → not_empty (stricter: ODCS `required` allows empty strings)")
    if prop.get("unique") is True:
        rules.append(_rule(field, "unique", message=f"{field} must be unique"))

    opts = prop.get("logicalTypeOptions") or {}
    if not isinstance(opts, dict):
        return rules
    ltype = str(prop.get("logicalType") or "string").lower()

    if opts.get("pattern"):
        rules.append(_rule(field, "regex", message=f"{field} must match pattern", pattern=str(opts["pattern"])))
    if opts.get("minLength") is not None:
        rules.append(_rule(field, "min_length", message=f"{field} must be at least {opts['minLength']} characters",
                           min_length=int(opts["minLength"])))
    if opts.get("maxLength") is not None:
        rules.append(_rule(field, "max_length", message=f"{field} must be at most {opts['maxLength']} characters",
                           max_length=int(opts["maxLength"])))

    if ltype in ("number", "integer"):
        lo = _num(opts.get("minimum"))
        hi = _num(opts.get("maximum"))
        # Exclusive bounds have no OpenDQV equivalent. Loosening them to
        # inclusive would pass a value the document rejects — skip, never loosen.
        for k in ("exclusiveMinimum", "exclusiveMaximum"):
            if opts.get(k) is not None:
                skipped.append(f"{field}.{k} (no strict-bound rule in OpenDQV; not loosened to inclusive)")
        if lo is not None and hi is not None:
            rules.append(_rule(field, "range", message=f"{field} must be between {lo:g} and {hi:g}",
                               min_value=lo, max_value=hi))
        elif lo is not None:
            rules.append(_rule(field, "min", message=f"{field} must be >= {lo:g}", min_value=lo))
        elif hi is not None:
            rules.append(_rule(field, "max", message=f"{field} must be <= {hi:g}", max_value=hi))
        if opts.get("multipleOf") is not None:
            skipped.append(f"{field}.multipleOf (no OpenDQV equivalent)")

    if ltype in ("date", "timestamp", "time"):
        fmt = opts.get("format")
        if fmt:
            strf = _from_jdk_format(str(fmt))
            if strf:
                rules.append(_rule(field, "date_format", message=f"{field} must match date format {fmt}", format=strf))
            else:
                skipped.append(f"{field}.format '{fmt}' (unsupported date pattern)")
        for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if opts.get(k) is not None:
                skipped.append(f"{field}.{k} (date bounds not supported)")
    return rules


def _library_rule(field: str, q: dict, skipped: list[str]) -> Optional[dict]:
    """Record-level reading of an ODCS library metric, or None (reason appended to skipped)."""
    metric = q.get("metric") or q.get("rule")   # `rule` is the deprecated 3.0 spelling
    severity = "warning" if str(q.get("severity", "error")).lower() in ("warning", "warn", "info") else "error"
    message = q.get("description") or ""
    must_be_zero = q.get("mustBe") in (0, 0.0, "0")
    if metric == "nullValues" and must_be_zero:
        return _rule(field, "not_empty", severity, message or f"{field} must not be null")
    if metric == "duplicateValues" and must_be_zero:
        return _rule(field, "unique", severity, message or f"{field} must be unique")
    if metric == "invalidValues" and must_be_zero:
        args = q.get("arguments") or {}
        values = args.get("validValues") if isinstance(args, dict) else None
        if isinstance(values, list) and values:
            return _rule(field, "allowed_values", severity, message or f"{field} must be one of the valid values",
                         allowed_values=[str(v) for v in values])
        if isinstance(args, dict) and args.get("pattern"):
            return _rule(field, "regex", severity, message or f"{field} must match pattern",
                         pattern=str(args["pattern"]))
        skipped.append(f"{field}.invalidValues (no validValues / pattern argument)")
        return None
    if metric == "missingValues" and must_be_zero:
        args = q.get("arguments") or {}
        sentinels = args.get("missingValues") if isinstance(args, dict) else None
        if isinstance(sentinels, list) and all(v is None or v == "" for v in sentinels):
            return _rule(field, "not_empty", severity, message or f"{field} must not be null or empty")
        skipped.append(f"{field}.missingValues (sentinels beyond null/'' cannot be honoured by not_empty)")
        return None
    skipped.append(f"{field}.{metric or '?'} (dataset-level metric, no record-level equivalent)")
    return None


def _quality_rules(field: str, quality: list, skipped: list[str]) -> tuple[list[dict], bool]:  # noqa: D401
    """Return (rules, had_opendqv_custom). Custom/opendqv entries take precedence."""
    custom: list[dict] = []
    other: list[dict] = []
    for q in quality or []:
        if not isinstance(q, dict):
            continue
        qtype = str(q.get("type", "")).lower()
        if qtype == "custom":
            if str(q.get("engine", "")).lower() == ODCS_ENGINE:
                custom.append(_sanitise_rule_dict(field, q.get("implementation")))
            else:
                skipped.append(f"{field}.custom (engine '{q.get('engine', '?')}')")
        elif qtype in ("library", "") or (qtype == "text" and (q.get("metric") or q.get("rule"))):
            # `library` is the ODCS default type; a `text` entry carrying a metric
            # is executed by the reference implementation, so treat it as library.
            r = _library_rule(field, q, skipped)
            if r:
                other.append(r)
        elif qtype in ("text", "sql"):
            skipped.append(f"{field}.{qtype} (not executable at record level)")
        else:
            skipped.append(f"{field}.{q.get('type') or '?'} (unknown quality type)")
    if custom:
        return custom, True
    return other, False


def _team_owner(team: Any) -> tuple[str, Optional[str]]:
    """(owner, owner_email) from ODCS 3.1 team object or 3.0 member list."""
    if isinstance(team, dict):
        members = team.get("members") or []
        first = next((m for m in members if isinstance(m, dict)), {})
        return str(team.get("name") or first.get("username") or ""), (first.get("username") or None)
    if isinstance(team, list):
        first = next((m for m in team if isinstance(m, dict)), {})
        return str(first.get("username") or first.get("name") or ""), (first.get("username") or None)
    return "", None


_ATTESTATION_KEYS = ("proposed_by", "proposed_at", "approved_by", "approved_at")


def _custom_property(doc: dict, key: str) -> Optional[Any]:
    for cp in doc.get("customProperties") or []:
        if isinstance(cp, dict) and cp.get("property") == key:
            return cp.get("value")
    return None


def import_odcs(contract_data: dict) -> dict:
    """
    Convert an ODCS 3.x contract dict to an OpenDQV contract dict.

    Raises ValueError for documents that are not ODCS 3.x or carry an invalid
    or forbidden rule definition (callers map this to HTTP 422).
    """
    if not isinstance(contract_data, dict):
        raise ValueError("ODCS document must be a mapping")
    api_version = str(contract_data.get("apiVersion", "")).strip()
    if api_version not in _SUPPORTED_API_VERSIONS:
        raise ValueError(
            f"Unsupported apiVersion '{api_version or '<missing>'}' — "
            f"OpenDQV reads ODCS {', '.join(sorted(_SUPPORTED_API_VERSIONS))}"
        )
    if str(contract_data.get("kind", "")) != "DataContract":
        raise ValueError("ODCS kind must be 'DataContract'")

    name = _sanitise_name(contract_data.get("name") or contract_data.get("id") or "imported_contract")
    version = str(contract_data.get("version", "1.0"))
    odcs_status = str(contract_data.get("status", "draft")).lower()
    status = _custom_property(contract_data, "opendqv.status") or _STATUS_FROM_ODCS.get(odcs_status, "draft")

    desc = contract_data.get("description")
    if isinstance(desc, dict):
        description = str(desc.get("purpose") or desc.get("usage") or "")
    else:
        description = str(desc or "")
    owner, owner_email = _team_owner(contract_data.get("team"))
    strict_schema = bool(_custom_property(contract_data, "opendqv.strict_schema") or False)
    declared_fields = _custom_property(contract_data, "opendqv.allowed_fields") or _custom_property(contract_data, "opendqv.fields") or []

    rules: list[dict] = []
    skipped: list[str] = []
    notes: list[str] = []
    seen_fields: dict[str, str] = {}

    schema = contract_data.get("schema") or []
    if not isinstance(schema, list):
        raise ValueError("ODCS schema must be a list of objects")
    for obj in schema:
        if not isinstance(obj, dict):
            continue
        obj_name = str(obj.get("name", "?"))
        for q in obj.get("quality") or []:
            if isinstance(q, dict):
                skipped.append(f"{obj_name}.{q.get('metric') or q.get('type') or '?'} (object-level check)")
        for prop in obj.get("properties") or []:
            if not isinstance(prop, dict) or not prop.get("name"):
                continue
            field = str(prop["name"])
            if field in seen_fields:
                skipped.append(f"{obj_name}.{field} (duplicate of {seen_fields[field]}.{field}; first definition kept)")
                continue
            seen_fields[field] = obj_name
            q_rules, from_custom = _quality_rules(field, prop.get("quality"), skipped)
            if from_custom:
                rules.extend(q_rules)          # authoritative; native projections are echoes
                continue
            # Native-derived rules: one per (type) per field — `required: true`
            # and a `nullValues mustBe 0` entry describe the same constraint.
            seen_types: set[str] = set()
            for r in _native_rules(field, prop, skipped, notes) + q_rules:
                if r["type"] in seen_types:
                    continue
                seen_types.add(r["type"])
                _validate_rule(field, r)     # fail closed (e.g. a pattern that does not compile)
                rules.append(r)

    deduped = rules
    # Rule names must be unique within a contract.
    used: set[str] = set()
    for r in deduped:
        base = r["name"]
        n, i = base, 2
        while n in used:
            n = f"{base}_{i}"
            i += 1
        r["name"] = n
        used.add(n)

    passthrough = {
        k: v for k, v in contract_data.items()
        if k in ("servers", "slaProperties", "slaDefaultElement", "roles", "support",
                 "price", "tags", "domain", "tenant", "dataProduct", "authoritativeDefinitions",
                 "contractCreatedTs")
    }

    contract: dict[str, Any] = {
        "name": name,
        "version": version,
        "status": status,
        "description": description,
        "owner": owner,
        "rules": deduped,
    }
    if owner_email:
        contract["owner_email"] = owner_email
    if strict_schema:
        contract["strict_schema"] = True
    if isinstance(declared_fields, list) and declared_fields:
        contract["allowed_fields"] = [str(f) for f in declared_fields]
    for key in _ATTESTATION_KEYS:   # 2.9.0: the approval trail survives the round trip
        value = _custom_property(contract_data, f"opendqv.{key}")
        if value is not None:
            contract[key] = str(value)

    return {
        "contract": contract,
        "skipped_checks": skipped,
        "import_notes": notes,
        "rule_count": len(deduped),
        "_odcs_metadata": passthrough,   # preserved for the API response, not evaluated
    }


def odcs_to_yaml(contract_data: dict, contract_name: Optional[str] = None) -> tuple[str, str]:
    """Import ODCS 3.x and return (contract_name, OpenDQV YAML string)."""
    result = import_odcs(contract_data)
    name = contract_name or result["contract"]["name"]
    result["contract"]["name"] = name
    yaml_str = yaml.dump({"contract": result["contract"]}, default_flow_style=False,
                         sort_keys=False, allow_unicode=True)
    return name, yaml_str


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

def _is_qualified(rule: dict) -> bool:
    """True when a rule only applies conditionally — native ODCS has no way to say that."""
    return bool(rule.get("condition"))


def _project_native(prop: dict, ltype: str, rule: dict) -> None:
    """Project an error-severity rule onto native ODCS fields when compatible.

    A native ODCS constraint is unconditional and un-negated. Rules qualified
    by ``condition``, ``negate`` or ``group_by`` would project as something
    stronger than (or the inverse of) what OpenDQV enforces, so they stay
    custom-only. (Ultrareview finding, 2026-09-02.)
    """
    if _is_qualified(rule):
        return
    rtype = rule["type"]
    opts: dict = prop.setdefault("logicalTypeOptions", {})
    if rtype in ("not_empty", "not_empty_string"):
        prop["required"] = True
    elif rtype == "unique":
        if not rule.get("group_by"):
            prop["unique"] = True
    elif ltype == "string":
        if rtype == "regex" and rule.get("pattern") and not rule.get("negate"):
            portable = _portable_pattern(rule["pattern"])
            if portable:
                opts["pattern"] = portable
        elif rtype == "min_length" and rule.get("min_length") is not None:
            opts["minLength"] = int(rule["min_length"])
        elif rtype == "max_length" and rule.get("max_length") is not None:
            opts["maxLength"] = int(rule["max_length"])
    elif ltype == "number":
        if rtype in ("min", "range") and rule.get("min_value") is not None:
            opts["minimum"] = rule["min_value"]
        if rtype in ("max", "range") and rule.get("max_value") is not None:
            opts["maximum"] = rule["max_value"]
    elif ltype in ("date", "timestamp"):
        if rtype == "date_format":
            jdk = _to_jdk_format(rule.get("format") or "%Y-%m-%d")
            if jdk:
                opts["format"] = jdk
    if not opts:
        prop.pop("logicalTypeOptions", None)


def _custom_entry(rule: dict) -> dict:
    entry: dict[str, Any] = {
        "type": "custom",
        "engine": ODCS_ENGINE,
        "name": rule["name"],
        "severity": rule.get("severity", "error"),
        "dimension": _DIMENSION.get(rule["type"], _DEFAULT_DIMENSION),
    }
    if rule.get("error_message"):
        entry["description"] = rule["error_message"]
    entry["implementation"] = rule
    return entry


def export_odcs(
    contract_name: str,
    rules: list,
    version: str = "1.0",
    status: str = "active",
    description: str = "",
    owner: str = "",
    owner_email: Optional[str] = None,
    odcs_metadata: Optional[dict] = None,
    strict_schema: bool = False,
    allowed_fields: list | None = None,
    attestation: dict | None = None,
) -> dict:
    """Export OpenDQV rules to an ODCS v3.1.0 contract dict (schema-valid).

    ``attestation`` (2.9.0): ``{proposed_by, proposed_at, approved_by, approved_at}``,
    carried as ``opendqv.<key>`` custom properties so the trail round-trips."""
    by_field: "OrderedDict[str, list[dict]]" = OrderedDict()
    for raw in rules:
        r = _rule_to_dict(raw)
        by_field.setdefault(r["field"], []).append(r)

    properties: list[dict] = []
    object_quality: list[dict] = []
    for field, field_rules in by_field.items():
        ltype = _infer_logical_type({r["type"] for r in field_rules})
        if ltype == "date" and any(r["type"] == "date_format" and _has_time(r.get("format")) for r in field_rules):
            ltype = "timestamp"
        prop: dict[str, Any] = {"name": field, "logicalType": ltype}
        quality: list[dict] = []
        for r in field_rules:
            if r.get("severity", "error") == "error":
                _project_native(prop, ltype, r)
                if r["type"] in ("allowed_values", "lookup") and r.get("allowed_values") and not _is_qualified(r) and not r.get("negate"):
                    # Values are quoted strings: the engine compares str(value),
                    # and a bare true/1.0 raises a CAST error in the reference implementation.
                    quality.append({
                        "type": "library", "metric": "invalidValues",
                        "arguments": {"validValues": [str(v) for v in r["allowed_values"]]},
                        "mustBe": 0, "severity": "error", "dimension": "conformity",
                        **({"description": r["error_message"]} if r.get("error_message") else {}),
                    })
                if r["type"] == "unique" and r.get("group_by") and not _is_qualified(r) and not object_quality:
                    # Compound uniqueness is object-level in ODCS. At most one per
                    # object — the reference implementation mis-reports a second.
                    object_quality.append({
                        "type": "library", "metric": "duplicateValues",
                        "arguments": {"properties": [field, *[str(g) for g in r["group_by"]]]},
                        "mustBe": 0, "severity": "error", "dimension": "uniqueness",
                        **({"description": r["error_message"]} if r.get("error_message") else {}),
                    })
            quality.append(_custom_entry(r))
        prop["quality"] = quality
        properties.append(prop)

    odcs_status = _STATUS_TO_ODCS.get(str(status).lower(), "draft")
    doc: dict[str, Any] = OrderedDict()
    doc["apiVersion"] = ODCS_API_VERSION
    doc["kind"] = "DataContract"
    doc["id"] = contract_name
    doc["name"] = contract_name
    doc["version"] = str(version)
    doc["status"] = odcs_status
    if description:
        doc["description"] = {"purpose": description}
    if owner:
        team: dict[str, Any] = {"name": owner}
        if owner_email:
            team["members"] = [{"username": owner_email, "role": "owner"}]
        doc["team"] = team
    doc["customProperties"] = [
        {"property": "opendqv.status", "value": str(status).lower()},
        {"property": "opendqv.engine", "value": "opendqv"},
    ]
    if strict_schema:
        doc["customProperties"].append({"property": "opendqv.strict_schema", "value": True})
    if allowed_fields:
        doc["customProperties"].append({"property": "opendqv.allowed_fields", "value": [str(f) for f in allowed_fields]})
    for key in _ATTESTATION_KEYS:
        if attestation and attestation.get(key) is not None:
            doc["customProperties"].append({"property": f"opendqv.{key}", "value": str(attestation[key])})
    schema_obj: dict[str, Any] = {"name": contract_name, "logicalType": "object", "properties": properties}
    if object_quality:
        schema_obj["quality"] = object_quality
    doc["schema"] = [schema_obj]
    if odcs_metadata:
        for k, v in odcs_metadata.items():
            if k not in doc:
                doc[k] = v
    return dict(doc)


def contract_to_odcs_yaml(
    contract_name: str,
    rules: list,
    version: str = "1.0",
    status: str = "active",
    description: str = "",
    owner: str = "",
    owner_email: Optional[str] = None,
    odcs_metadata: Optional[dict] = None,
    strict_schema: bool = False,
    allowed_fields: list | None = None,
    attestation: dict | None = None,
) -> str:
    """Export OpenDQV contract to ODCS v3.1.0 YAML string."""
    doc = export_odcs(contract_name, rules, version=version, status=status, description=description,
                      owner=owner, owner_email=owner_email, odcs_metadata=odcs_metadata,
                      strict_schema=strict_schema, allowed_fields=allowed_fields, attestation=attestation)
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)
