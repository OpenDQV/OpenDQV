"""
Rule model and YAML parsing.

Rules now include severity (error = block, warning = allow but flag).
Field aliases let YAML use 'min'/'max' which map to min_value/max_value.

Supported rule types:
  not_empty         — field must be present and non-empty
  regex             — field must match a regular expression pattern
                      set negate: true to require the field does NOT match
  min               — numeric field must be >= min_value
  max               — numeric field must be <= max_value
  range             — numeric field must be between min_value and max_value
  min_length        — string length must be >= min_length
  max_length        — string length must be <= max_length
  date_format       — field must be a parseable date/datetime string
  unique            — field must be unique across the batch (batch mode only)
                      set group_by: [field, ...] to scope uniqueness within groups
  min_age           — date field implies minimum age (calendar years)
  max_age           — date field implies maximum age (calendar years)
  compare           — cross-field comparison: field <op> compare_to
                      ops: gt, lt, gte, lte, eq, neq
                      works with numbers, ISO date strings, and plain strings
                      compare_to accepts "today"/"now" as sentinel values
                      Datetime fields compared with compare_to: now or compare_to: today
                      must be in ISO 8601 format. Naive datetimes (no timezone offset)
                      are treated as UTC.
  required_if       — field is required when another field has a specific value
                      required_if: {field: panel_type, value: DIGITAL}
  lookup            — field value must appear in a reference list
                      lookup_file: /path/to/ids.txt          (local file, one value per line)
                      lookup_file: /path/to/ids.csv          + lookup_field: column_name (CSV)
                      lookup_file: https://host/endpoint     (HTTP GET — JSON array or newline text)
                      cache_ttl: 300                         (HTTP cache TTL seconds, default 300)
                      lookup_auth_header: "Bearer ${TOKEN}"  (authenticated HTTP endpoints)
                      set all_of: true to validate each element in a list field
  checksum          — validates identifier check digits
                      (IBAN, GTIN/GS1, NHS, ISIN, LEI, VIN, ISRC, CPF)
                      checksum_algorithm: mod10_gs1 | iban_mod97 | isin_mod11 |
                                          lei_mod97 | vin_mod11 | isrc_luhn |
                                          cpf_mod11 | nhs_mod11
  cross_field_range — value must be between two other fields
                      cross_min_field: lower_bound_field
                      cross_max_field: upper_bound_field
  field_sum         — sum of named fields must equal target value
                      sum_fields: [field_a, field_b, ...]
                      sum_equals: <target>   sum_tolerance: <epsilon, default 0.0>
  forbidden_if      — field must be absent when condition is met
                      forbidden_if: {field: status, value: CANCELLED}
  conditional_value — field must equal specific value when condition is met
                      must_equal: "PENDING"  condition: {field: status, value: REVIEW}
  date_diff         — difference between two date fields within range (P2)
                      date_diff_field: other_field  date_diff_unit: days|years
                      uses min_value/max_value for the allowed range
  ratio_check       — ratio of two fields within range (P2)
                      ratio_numerator: field_a  ratio_denominator: field_b
                      uses min_value/max_value for the allowed range
  geospatial_bounds — lat/lon pair must fall within a geographic bounding box
                      geo_lon_field: field containing longitude
                      geo_min_lat/geo_max_lat: latitude bounds
                      geo_min_lon/geo_max_lon: longitude bounds
"""

import logging
import re
try:
    import regex as _regex_lib
    _HAS_REGEX_LIB = True
except ImportError:  # pragma: no cover
    _regex_lib = None
    _HAS_REGEX_LIB = False
import yaml
from pydantic import BaseModel, Field, model_validator
from typing import Any, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


_BUILTIN_PATTERNS = {
    "builtin:semver": r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([\w.-]+))?(?:\+([\w.-]+))?$",
    "builtin:ipv4": r"^((25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(25[0-5]|2[0-4]\d|[01]?\d\d?)$",
    "builtin:ipv6": r"^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$",
    "builtin:cve_id": r"^CVE-\d{4}-\d{4,}$",
    "builtin:smpte-timecode": r"^\d{2}:\d{2}:\d{2}[:;]\d{2}$",
    "builtin:did": r"^did:[a-z]+:[a-zA-Z0-9._-]+$",
    "builtin:ean13": r"^\d{13}$",
    "builtin:isbn13": r"^97[89]\d{10}$",
    "builtin:uuid": r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    "builtin:email": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    "builtin:url": r"^https?://[^\s]+$",
}


class Severity(str, Enum):
    ERROR = "error"      # blocks the record
    WARNING = "warning"  # allows but flags


class ContractStatus(str, Enum):
    DRAFT = "draft"            # being authored/tested — not usable in production
    REVIEW = "review"          # submitted for approval — frozen until approved or rejected
    ACTIVE = "active"          # live — source systems can validate against it
    ARCHIVED = "archived"      # still works but callers should migrate


class Rule(BaseModel):
    name: str
    description: str = ""
    type: str
    field: str
    pattern: Optional[str] = None
    min_value: Optional[float] = Field(None, alias="min")
    max_value: Optional[float] = Field(None, alias="max")
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    min_age: Optional[int] = None
    max_age: Optional[int] = None
    format: Optional[str] = None
    severity: Severity = Severity.ERROR
    error_message: str = "Validation failed"

    # Cross-field comparison — compare this field's value against another field.
    # type: compare  compare_to: other_field  compare_op: gt|lt|gte|lte|eq|neq
    # Accepts both word form (gt) and symbol form (>); normalised at parse time.
    compare_to: Optional[str] = None
    compare_op: Optional[str] = None  # "gt", "lt", "gte", "lte", "eq", "neq"

    # Conditional required — this field is required when another field equals a value.
    # type: required_if  required_if: {field: panel_type, value: DIGITAL}
    required_if: Optional[dict] = None

    # Conditional constraint — apply this rule only when a condition on another field is met.
    # Applies to any rule type. Examples:
    #   condition: {field: transaction_type, not_value: CREDIT}  → skip rule for credits
    #   condition: {field: region, value: EU}                    → apply rule only in EU
    condition: Optional[dict] = None

    # Inline allowed values — type: allowed_values
    # Validates that the field value is one of the listed values.
    # Avoids the need for a separate lookup file for short, stable lists.
    # type: allowed_values  allowed_values: [active, inactive, pending]
    allowed_values: Optional[list] = None

    # File-based or REST-based lookup — value must appear in a reference list.
    # type: lookup  lookup_file: /path/to/ids.txt          (local file, one value per line)
    # type: lookup  lookup_file: https://host/endpoint     (HTTP GET, JSON array or newline-delimited)
    # For CSV: also set lookup_field: column_name
    # cache_ttl: seconds to cache the fetched result (HTTP only; default 300)
    lookup_file: Optional[str] = None
    lookup_field: Optional[str] = None  # CSV column name; if None, one value per line
    cache_ttl: Optional[int] = None     # HTTP cache TTL in seconds (default 300)

    # Federation / inheritance fields — populated when a rule originates from an upstream authority.
    # Community nodes may add rules freely; they cannot weaken an inherited rule.
    severity_floor: Optional[Severity] = None  # minimum severity; local nodes cannot downgrade below this
    provenance: Optional[dict] = None          # {"authority_node": str, "lsn": int}
    inherited: bool = False                    # True when received from an upstream authority node
    # federation_tier: REGULATORY / COMMERCIAL / COMMUNITY
    federation_tier: Optional[str] = None

    # Checksum validation — type: checksum
    # algorithm: mod10_gs1 | iban_mod97 | isin_mod11 | lei_mod97 | vin_mod11 | isrc_luhn | cpf_mod11 | nhs_mod11
    checksum_algorithm: Optional[str] = None

    # Cross-field range — type: cross_field_range
    # The field value must be between the values of cross_min_field and cross_max_field (from same record)
    cross_min_field: Optional[str] = None   # field name whose value is the lower bound
    cross_max_field: Optional[str] = None   # field name whose value is the upper bound

    # Grouped uniqueness — type: unique with group_by
    # e.g. unique within settlement_period groups
    group_by: Optional[list] = None  # list of field names

    # Field sum rule — type: field_sum
    # Sum of listed fields must equal target (within optional tolerance)
    sum_fields: Optional[list] = None    # list of field names to sum
    sum_equals: Optional[float] = None   # target value
    sum_tolerance: Optional[float] = None  # default 0.0 (exact match)

    # Negate regex — type: regex with negate: true
    # Field must NOT match the pattern
    negate: bool = False

    # Forbidden if — type: forbidden_if
    # Field must be absent/None/empty when another field equals a value
    # forbidden_if: {field: status, value: CANCELLED}
    forbidden_if: Optional[dict] = None

    # Conditional value / must equal if — type: conditional_value
    # Field must equal a specific value when a condition is met
    # must_equal: "PENDING"  condition: {field: status, value: REVIEW}
    must_equal: Optional[str] = None

    # Date diff — type: date_diff (P2 but add model field now)
    # Difference in days/years between two date fields; uses min_value/max_value for allowed range
    date_diff_field: Optional[str] = None   # other field for diff
    date_diff_unit: Optional[str] = None    # "days" or "years"

    # Age match — type: age_match
    # Validates that declared age is consistent with computed age from DOB
    dob_field: Optional[str] = None        # age_match: field holding date-of-birth
    age_tolerance: Optional[int] = None    # age_match: allowed difference in years (default 0 — exact match)

    # Ratio check — type: ratio_check (P2)
    # field_a / field_b within range; uses min_value/max_value for allowed range
    ratio_numerator: Optional[str] = None
    ratio_denominator: Optional[str] = None

    # Geospatial bounds — type: geospatial_bounds
    # Validates lat/long fields against a geographic bounding box.
    # The field being validated must contain the LATITUDE value.
    # lon_field specifies the field containing the LONGITUDE value.
    geo_lon_field: Optional[str] = None      # field name containing longitude
    geo_min_lat: Optional[float] = None      # minimum latitude (-90 to 90)
    geo_max_lat: Optional[float] = None      # maximum latitude (-90 to 90)
    geo_min_lon: Optional[float] = None      # minimum longitude (-180 to 180)
    geo_max_lon: Optional[float] = None      # maximum longitude (-180 to 180)

    # HTTP lookup auth — Bearer token for authenticated endpoints
    # e.g. "Bearer ${OFAC_API_KEY}" — env var substitution performed at runtime
    lookup_auth_header: Optional[str] = None

    # All_of for list lookup — type: lookup with all_of: true
    # Validate each element in a list field against the lookup
    all_of: bool = False

    # Algorithm hint for compare rule — "semver" for semantic version comparison
    algorithm: Optional[str] = None

    # Compiled regex — populated at parse time, excluded from serialisation.
    # Eliminates per-call re.compile() overhead and makes cache hits explicit.
    compiled_pattern: Optional[Any] = Field(default=None, exclude=True, repr=False)

    # ── Hot-path caches (populated in _post_parse, excluded from serialisation) ──
    # These avoid repeated Pydantic attribute access / Enum .value calls on every
    # validate_record() invocation. Zero behaviour change — pure speed.
    cached_has_condition: bool = Field(default=False, exclude=True, repr=False)
    cached_severity_value: str = Field(default="error", exclude=True, repr=False)
    cached_error_code: str = Field(default="", exclude=True, repr=False)
    cached_has_age_constraint: bool = Field(default=False, exclude=True, repr=False)

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}

    _COMPARE_OP_ALIASES: dict = {
        ">": "gt", "<": "lt", ">=": "gte", "<=": "lte", "=": "eq", "!=": "neq",
    }

    @model_validator(mode="after")
    def _post_parse(self) -> "Rule":
        """Pre-compile regex patterns and normalise compare_op symbols to word form.
        Warn on misconfigured rules that would be no-ops or fail silently."""
        if self.type == "regex":
            if not self.pattern:
                logger.warning(
                    "Rule '%s' (type=regex) has no pattern — it will fail every record. "
                    "Add a pattern field to make this rule functional.",
                    self.name,
                )
            else:
                expanded = _BUILTIN_PATTERNS.get(self.pattern, self.pattern)
                self.compiled_pattern = (
                    _regex_lib.compile(expanded) if _HAS_REGEX_LIB else re.compile(expanded)
                )
        if self.type == "allowed_values" and not self.allowed_values:
            logger.warning(
                "Rule '%s' (type=allowed_values) has no allowed_values list — it will skip validation. "
                "Add an allowed_values field with a list of permitted values.",
                self.name,
            )
        if self.type == "lookup" and not self.lookup_file:
            logger.warning(
                "Rule '%s' (type=lookup) has no lookup_file — it will skip validation. "
                "Add a lookup_file field.",
                self.name,
            )
        if self.type == "checksum" and not self.checksum_algorithm:
            logger.warning(
                "Rule '%s' (type=checksum) has no checksum_algorithm — it will skip validation. "
                "Add a checksum_algorithm field.",
                self.name,
            )
        if self.type == "date_diff" and not self.date_diff_field:
            logger.warning(
                "Rule '%s' (type=date_diff) has no date_diff_field — it will skip validation. "
                "Add a date_diff_field.",
                self.name,
            )
        if self.compare_op and self.compare_op in self._COMPARE_OP_ALIASES:
            self.compare_op = self._COMPARE_OP_ALIASES[self.compare_op]
        # SEC-004: Validate field name is safe for use as a SQL identifier in DuckDB
        # batch queries. Field names are double-quoted in SQL, but a name containing
        # a double-quote character would break the quoting and allow SQL injection.
        # Allow: letters, digits, underscore, hyphen, space, dot (all safe when double-quoted).
        # Reject: double-quote, backslash, null byte, semicolon, and other control chars.
        _UNSAFE_FIELD_CHARS = re.compile(r'["\\\x00;\x01-\x08\x0b-\x1f\x7f]')
        if _UNSAFE_FIELD_CHARS.search(self.field):
            raise ValueError(
                f"Rule '{self.name}': field name '{self.field}' contains characters "
                f"not permitted in a SQL identifier (double-quote, backslash, "
                f"semicolon, or control characters)."
            )
        # Hot-path caches — avoid repeated Pydantic/Enum access per validation call
        self.cached_has_condition = bool(self.condition)
        self.cached_severity_value = self.severity.value
        self.cached_error_code = f"OPENDQV_{self.type.upper()}_001"
        self.cached_has_age_constraint = self.min_age is not None or self.max_age is not None
        return self


def parse_rules(yaml_str: str) -> List[Rule]:
    """Parse a YAML string containing a flat list under 'rules:' key."""
    data = yaml.safe_load(yaml_str) or {}
    raw_rules = data.get("rules", [])
    if isinstance(raw_rules, list):
        return [Rule(**r) for r in raw_rules]
    return []
