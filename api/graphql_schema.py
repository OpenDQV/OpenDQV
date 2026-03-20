"""
GraphQL schema for OpenDQV.

Provides the same validation capabilities as the REST API via GraphQL.
Uses strawberry-graphql with FastAPI integration.
"""

import time
import logging
from typing import Optional

import strawberry
from strawberry.scalars import JSON

logger = logging.getLogger(__name__)

# Registry is set by main.py at startup
_registry = None


def set_registry(reg):
    global _registry
    _registry = reg


# ── Types ────────────────────────────────────────────────────────────

@strawberry.type
class FieldError:
    field: str
    rule: str
    message: str
    severity: str


@strawberry.type
class ValidateResult:
    valid: bool
    record_id: Optional[str]
    errors: list[FieldError]
    warnings: list[FieldError]
    contract: str
    version: str


@strawberry.type
class BatchResultItem:
    index: int
    valid: bool
    errors: list[FieldError]
    warnings: list[FieldError]


@strawberry.type
class BatchSummary:
    total: int
    passed: int
    failed: int
    error_count: int
    warning_count: int
    rule_failure_counts: JSON = strawberry.field(
        default_factory=dict,
        description="Per-rule failure counts: {rule_name: count}",
    )


@strawberry.type
class BatchValidateResult:
    summary: BatchSummary
    results: list[BatchResultItem]
    contract: str
    version: str
    owner: str = ""


@strawberry.type
class RuleInfo:
    name: str
    type: str
    field: str
    severity: str
    error_message: str


@strawberry.type
class ContractInfo:
    name: str
    version: str
    description: str
    owner: str
    status: str
    rule_count: int
    asset_id: Optional[str] = None


@strawberry.type
class ContractDetail:
    name: str
    version: str
    description: str
    owner: str
    status: str
    rules: list[RuleInfo]
    contexts: list[str]
    asset_id: Optional[str] = None


# ── Queries ──────────────────────────────────────────────────────────

@strawberry.type
class Query:
    @strawberry.field(description="List all available data contracts")
    def contracts(self) -> list[ContractInfo]:
        return [
            ContractInfo(**c)
            for c in _registry.list_contracts()
        ]

    @strawberry.field(description="Get full detail of a specific data contract")
    def contract(self, name: str, version: str = "latest") -> Optional[ContractDetail]:
        c = _registry.get(name, version)
        if not c:
            return None
        return ContractDetail(
            name=c.name,
            version=c.version,
            description=c.description,
            owner=c.owner,
            status=c.status.value,
            rules=[
                RuleInfo(
                    name=r.name, type=r.type, field=r.field,
                    severity=r.severity.value, error_message=r.error_message,
                )
                for r in c.rules
            ],
            contexts=list(c.contexts.keys()),
            asset_id=c.asset_id,
        )


# ── Mutations ────────────────────────────────────────────────────────

@strawberry.type
class Mutation:
    @strawberry.mutation(description="Validate a single record against a data contract")
    def validate(
        self,
        record: JSON,
        contract: str,
        version: str = "latest",
        context: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> ValidateResult:
        from core.validator import validate_record

        start = time.monotonic()
        c = _registry.get(contract, version)
        if not c:
            return ValidateResult(
                valid=False,
                record_id=record_id,
                errors=[FieldError(
                    field="_contract",
                    rule="_system",
                    message=f"Contract '{contract}' version '{version}' not found",
                    severity="error",
                )],
                warnings=[],
                contract=contract,
                version=version,
            )

        rules = _registry.get_rules_with_context(c, context)
        result = validate_record(record, rules)

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info("graphql validate: %s v%s valid=%s %.1fms", c.name, c.version, result["valid"], elapsed_ms)

        return ValidateResult(
            valid=result["valid"],
            record_id=record_id,
            errors=[FieldError(**e) for e in result["errors"]],
            warnings=[FieldError(**w) for w in result["warnings"]],
            contract=c.name,
            version=c.version,
        )

    @strawberry.mutation(description="Validate a batch of records against a data contract")
    def validate_batch(
        self,
        records: JSON,
        contract: str,
        version: str = "latest",
        context: Optional[str] = None,
    ) -> BatchValidateResult:
        from core.validator import validate_batch as vb

        start = time.monotonic()
        c = _registry.get(contract, version)
        if not c:
            return BatchValidateResult(
                summary=BatchSummary(total=0, passed=0, failed=0, error_count=1, warning_count=0),
                results=[],
                contract=contract,
                version=version,
                owner="",
            )

        rules = _registry.get_rules_with_context(c, context)
        result = vb(records, rules)

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info("graphql validate_batch: %s v%s %d records %.1fms",
                     c.name, c.version, result["summary"]["total"], elapsed_ms)

        return BatchValidateResult(
            summary=BatchSummary(**result["summary"]),
            results=[
                BatchResultItem(
                    index=r["index"],
                    valid=r["valid"],
                    errors=[FieldError(**e) for e in r["errors"]],
                    warnings=[FieldError(**w) for w in r["warnings"]],
                )
                for r in result["results"]
            ],
            contract=c.name,
            version=c.version,
            owner=c.owner or "",
        )


# ── Schema ───────────────────────────────────────────────────────────

schema = strawberry.Schema(query=Query, mutation=Mutation)
