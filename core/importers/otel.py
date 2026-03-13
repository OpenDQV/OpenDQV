"""
OpenTelemetry (OTel) Semantic Conventions schema importer for OpenDQV.

Converts OTel attribute group definitions to OpenDQV validation rules.
Useful for validating telemetry data (spans, metrics, logs) against
OTel semantic conventions before ingestion into observability platforms.

Reference: https://opentelemetry.io/docs/specs/semconv/
"""

import re as _re
import yaml as _yaml
from typing import Union


def _scan_rules_for_lookup_file(rules: list) -> None:
    """
    Scan generated rules for lookup_file values and validate each path for safety.

    Raises ValueError if any lookup_file would fail the path traversal check.
    """
    from core.validator import _check_lookup_path_safe
    for rule in rules:
        lookup_file = rule.get("lookup_file")
        if lookup_file:
            try:
                _check_lookup_path_safe(lookup_file)
            except ValueError as exc:
                raise ValueError(
                    f"Importer security: unsafe lookup_file in generated rule '{rule.get('name', '?')}': {exc}"
                ) from exc


# Known OTel enum attributes with their valid values.
# Values current as of OTel semconv v1.25 — will go stale as the spec evolves.
_KNOWN_ENUMS = {
    "http.request.method": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT"],
    "http.method": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "CONNECT"],  # deprecated in OTel 1.21
    "db.system": [
        "postgresql", "mysql", "sqlite", "mongodb", "redis", "cassandra", "elasticsearch",
        "clickhouse", "cockroachdb", "dynamodb", "spanner", "cosmosdb", "bigtable",
    ],
    "messaging.system": ["kafka", "rabbitmq", "activemq", "aws_sqs", "azure_service_bus"],
    "cloud.provider": ["aws", "azure", "gcp", "alibaba_cloud", "tencent_cloud"],
    "faas.trigger": ["datasource", "http", "pubsub", "timer", "other"],
    "net.transport": ["ip_tcp", "ip_udp", "pipe", "inproc", "other"],
}

_KNOWN_RANGES = {
    "http.response.status_code": (100, 599),
    "http.status_code": (100, 599),
    "net.peer.port": (1, 65535),
    "net.host.port": (1, 65535),
    "http.request_content_length": (0, None),
    "http.response_content_length": (0, None),
}


def import_otel(source: Union[str, dict]) -> dict:
    """
    Parse an OTel semantic convention YAML/dict and return OpenDQV rules.

    source: YAML string or parsed dict
    Returns: {"rules": [...], "metadata": {...}}
    """
    if isinstance(source, str):
        data = _yaml.safe_load(source)
    else:
        data = source

    groups = data.get("groups", [])
    rules = []

    for group in groups:
        attributes = group.get("attributes", [])

        for attr in attributes:
            attr_id = attr.get("id", "")
            field = attr_id.replace(".", "_").replace("-", "_")
            brief = attr.get("brief", attr_id)
            req_level = attr.get("requirement_level", "")
            attr_type = attr.get("type", "string")

            is_required = req_level in ("required",) or (
                isinstance(req_level, dict) and req_level.get("conditionally_required")
            )

            rule_base = {
                "field": field,
                "severity": "error" if is_required else "warning",
                "description": brief,
            }

            if is_required:
                rules.append({
                    **rule_base,
                    "name": f"{field}_required",
                    "type": "not_empty",
                    "error_message": f"{attr_id} is required by OTel spec",
                })

            # Known enum — emitted as regex (validator requires lookup_file; no inline lookup support)
            if attr_id in _KNOWN_ENUMS:
                vals = _KNOWN_ENUMS[attr_id]
                pattern = "^(" + "|".join(_re.escape(v) for v in vals) + ")$"
                rules.append({
                    **rule_base,
                    "name": f"{field}_values",
                    "type": "regex",
                    "pattern": pattern,
                    "error_message": f"{attr_id} must be a recognised OTel value",
                })
            elif isinstance(attr_type, dict) and "allow_custom_values" not in str(attr_type):
                enum_vals = attr_type.get("members", [])
                if enum_vals:
                    vals = [m.get("id", m.get("value", "")) for m in enum_vals if isinstance(m, dict)]
                    str_vals = [str(v) for v in vals if v]
                    if str_vals:
                        pattern = "^(" + "|".join(_re.escape(v) for v in str_vals) + ")$"
                        rules.append({
                            **rule_base,
                            "name": f"{field}_values",
                            "type": "regex",
                            "pattern": pattern,
                            "error_message": f"{attr_id} must be a recognised value",
                        })

            # Known numeric ranges
            if attr_id in _KNOWN_RANGES:
                lo, hi = _KNOWN_RANGES[attr_id]
                range_rule = {
                    **rule_base,
                    "name": f"{field}_range",
                    "type": "range" if hi else "min",
                    "error_message": f"{attr_id} out of valid range",
                }
                if lo is not None:
                    range_rule["min_value"] = float(lo)
                if hi is not None:
                    range_rule["max_value"] = float(hi)
                    range_rule["type"] = "range"
                rules.append(range_rule)

    return {
        "rules": rules,
        "metadata": {"source": "otel", "group_count": len(groups)},
    }


def otel_to_yaml(source: Union[str, dict], contract_name: str = "otel_telemetry") -> str:
    """Convert OTel schema to OpenDQV contract YAML."""
    parsed = import_otel(source)
    # SEC-006: validate any generated lookup_file paths for path traversal
    _scan_rules_for_lookup_file(parsed["rules"])
    contract = {
        "contract": {
            "name": contract_name,
            "version": "1.0",
            "description": "Imported from OpenTelemetry semantic conventions",
            "status": "draft",
            "rules": parsed["rules"],
        }
    }
    return _yaml.dump(contract, default_flow_style=False, sort_keys=False)
