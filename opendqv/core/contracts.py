"""
Data Contract loading and versioning.

A data contract defines the quality rules for a business entity (e.g. Customer, Order).
Contracts are versioned YAML files stored in the contracts/ directory.

File naming: {name}.yaml or {name}_v{version}.yaml
"""

import copy
import hashlib
import json
import re as _re
import sqlite3
import yaml
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, field_validator
from .rule_parser import Rule, Severity, ContractStatus, RULE_TYPES

# Canonical contract-name charset. Letters, digits, hyphen, underscore only —
# no path separators, no dots, so a name can never escape ``contracts_dir``
# (SEC-002/006). The REST layer (``api/deps._CONTRACT_NAME_RE``) aliases this;
# every core write path that takes a caller-supplied name must check it.
CONTRACT_NAME_RE = _re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class UnknownContextError(ValueError):
    """Raised when a named context is specified but does not exist in the contract."""


def _read_meta(raw: dict) -> dict:
    """Read lifecycle status + provenance written at the top level of a YAML doc.

    Used by the legacy and onboarding formats, which have no `contract:` block
    (CRT177 Tier 2). Absent keys are omitted so DataContract's own defaults
    apply — notably `status`, which must not be forced when the file never
    carried one.
    """
    out: dict = {}
    if raw.get("status") is not None:
        out["status"] = raw["status"]
    for field in ("source", "proposed_by", "proposed_at", "approved_by",
                  "approved_at", "rejected_by", "rejected_at", "rejection_reason"):
        if raw.get(field) is not None:
            out[field] = raw[field]
    return out


class ContractImmutableError(ValueError):
    """Rule mutation attempted on a contract whose state forbids it (ACTIVE or REVIEW)."""


class ContractPersistenceError(RuntimeError):
    """Raised when a lifecycle transition could not be written to the contract's YAML file.

    The transition has already been applied in memory and recorded in history,
    so the caller must be told the change is not durable — it will revert on the
    next reload. Silently swallowing this is what allowed an approval to live
    only in memory (CRT177 Tier 2).
    """
from .storage import ContractHistoryBackend

import opendqv.config as config

logger = logging.getLogger(__name__)


def check_inheritance_invariant(base_rule: "Rule", proposed_rule: "Rule") -> list[str]:
    """
    Validate that a proposed rule change does not weaken an inherited rule.

    Returns a list of violation descriptions. An empty list means the invariant holds.
    Called before applying any local rule mutation when base_rule.inherited is True.

    Local nodes may ONLY ADD constraints (tighten). They may NEVER:
      - Downgrade severity below severity_floor
      - Widen a numeric min/max range
      - Widen string length constraints
      - Alter an inherited regex pattern
      - Change the rule type
    """
    if not base_rule.inherited:
        return []

    violations = []
    severity_order = {Severity.ERROR: 2, Severity.WARNING: 1}

    # Severity floor check
    floor = base_rule.severity_floor or base_rule.severity
    if severity_order.get(proposed_rule.severity, 0) < severity_order.get(floor, 0):
        authority = (base_rule.provenance or {}).get("authority_node", "authority")
        violations.append(
            f"Rule '{base_rule.name}': cannot downgrade severity from "
            f"'{floor.value}' to '{proposed_rule.severity.value}' "
            f"(severity_floor set by {authority})"
        )

    # Numeric range widening
    if base_rule.min_value is not None and proposed_rule.min_value is not None:
        if proposed_rule.min_value < base_rule.min_value:
            violations.append(
                f"Rule '{base_rule.name}': cannot lower min from "
                f"{base_rule.min_value} to {proposed_rule.min_value}"
            )
    if base_rule.max_value is not None and proposed_rule.max_value is not None:
        if proposed_rule.max_value > base_rule.max_value:
            violations.append(
                f"Rule '{base_rule.name}': cannot raise max from "
                f"{base_rule.max_value} to {proposed_rule.max_value}"
            )

    # String length widening
    if base_rule.min_length is not None and proposed_rule.min_length is not None:
        if proposed_rule.min_length < base_rule.min_length:
            violations.append(
                f"Rule '{base_rule.name}': cannot lower min_length from "
                f"{base_rule.min_length} to {proposed_rule.min_length}"
            )
    if base_rule.max_length is not None and proposed_rule.max_length is not None:
        if proposed_rule.max_length > base_rule.max_length:
            violations.append(
                f"Rule '{base_rule.name}': cannot raise max_length from "
                f"{base_rule.max_length} to {proposed_rule.max_length}"
            )

    # Pattern alteration
    if base_rule.pattern is not None and proposed_rule.pattern is not None:
        if proposed_rule.pattern != base_rule.pattern:
            violations.append(
                f"Rule '{base_rule.name}': cannot alter regex pattern on inherited rule"
            )

    # Type change
    if proposed_rule.type != base_rule.type:
        violations.append(
            f"Rule '{base_rule.name}': cannot change rule type from "
            f"'{base_rule.type}' to '{proposed_rule.type}'"
        )

    return violations


_GENESIS_HASH = "0" * 64

# Hash domain marker. Bumped when the set of fields covered by entry_hash /
# content_hash changes. Pre-v2 chain entries are scrubbed on first boot
# under v2.3.0 — see ContractHistory._scrub_pre_v2_entries.
_HASH_DOMAIN_VERSION = 2

# Canonical contract field set covered by the v2 hash domain. Single source of
# truth — referenced by the linter guard test in tests/test_hash_domain_guard.py
# to detect future field additions that aren't covered by the hash.
_HASH_DOMAIN_CONTENT_FIELDS = (
    "name", "version", "status",
    "owner", "owner_email", "owner_team", "asset_id", "description",
    "downstream_consumers",
    "rules", "contexts",
    # CRT180: strict-schema flag + declared-field allow-list. They join the
    # canonical payload ONLY when set, so every pre-CRT180 contract keeps its
    # v2 hashes byte-for-byte (no domain bump); a contract that turns strict
    # on gets a new hash, which is correct — it is new enforcement content.
    "strict_schema", "allowed_fields",
)


def _canonical_json(obj) -> str:
    """Deterministic JSON serialisation for hash inputs.

    sort_keys=True makes serialisation independent of dict construction order.
    separators strips JSON's default whitespace. ensure_ascii=False preserves
    UTF-8 in description text (£, €, §, …) so the hash reflects the file
    bytes rather than escape-encoded representations that drift across
    serialisers.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_payload_parts(
    contract_name: str, version: str, status: str,
    owner: str, owner_email: Optional[str], owner_team: Optional[str],
    asset_id: Optional[str], description: str,
    downstream_consumers: list,
    rules, contexts,
    strict_schema: bool = False, allowed_fields: list | None = None,
) -> list[str]:
    """Canonical JSON parts for content fields, in fixed order.

    Used by both _compute_entry_hash and _compute_content_hash to guarantee
    that any field present in one is present in the other. Field order
    matches _HASH_DOMAIN_CONTENT_FIELDS.
    """
    parts = [
        _canonical_json(contract_name),
        _canonical_json(version),
        _canonical_json(status),
        _canonical_json(owner),
        _canonical_json(owner_email),
        _canonical_json(owner_team),
        _canonical_json(asset_id),
        _canonical_json(description),
        _canonical_json(downstream_consumers),
        _canonical_json(rules),
        _canonical_json(contexts),
    ]
    if strict_schema or allowed_fields:
        parts.append(_canonical_json({"strict_schema": bool(strict_schema), "allowed_fields": sorted(allowed_fields or [])}))
    return parts


def _compute_effective_rule_hash(rules) -> str:
    """SHA-256 over the canonical-JSON serialisation of the resolved Rule set.

    The 3-hash triplet (entry_hash, content_hash, contract_hash) is computed
    over the static contract definition. Two validate calls with different
    contexts (e.g. context=billing vs context=operations) produce the same
    triplet even though they ran different rule sets — a CRT170-J violation
    that breaks audit-replay for contextualised validations.

    effective_rule_hash closes that gap: it hashes the Rule objects AS USED
    by the validator on this call, after context overrides have been
    resolved. Two calls that produced different rule sets — different
    thresholds, different severity, different error messages, different
    fields, different rule names — get different effective_rule_hash
    values, so the audit trail can prove which rule set actually ran.

    Hash design choice (v2.3.17 F-J): full canonical serialisation of each
    Rule via model_dump(), not rule-names-only. Rule names alone would miss
    the case where a context override changes a threshold or severity but
    not a name — which is exactly the failure mode Persona B reported. Full
    serialisation catches every override that materially changes
    enforcement behaviour. Trade-off: cosmetic-only changes to a rule
    (description rewrite with no enforcement impact) also bump the hash.
    That is the correct trade-off for an audit field: false positives are
    cheaper than false negatives.

    The hash is order-sensitive on the rule list — caller passes rules in
    the same order the validator iterates them, which is the natural order
    after override resolution.
    """
    serialised = [r.model_dump(by_alias=True, mode="json") for r in (rules or [])]
    return hashlib.sha256(_canonical_json(serialised).encode("utf-8")).hexdigest()


def _compute_content_hash(
    contract_name: str, version: str, status: str,
    owner: str, owner_email: Optional[str], owner_team: Optional[str],
    asset_id: Optional[str], description: str,
    downstream_consumers: list,
    rules, contexts,
    strict_schema: bool = False, allowed_fields: list | None = None,
) -> str:
    """SHA-256 over content fields only — excludes prev_hash, node_id, updated_at.

    Two boots of byte-identical YAML produce identical content_hash regardless
    of timestamp or node identity. Use this for content-equality questions
    (audit-packet diffs, replay verification).
    """
    parts = _content_payload_parts(
        contract_name, version, status, owner, owner_email, owner_team,
        asset_id, description, downstream_consumers, rules, contexts,
        strict_schema=strict_schema, allowed_fields=allowed_fields,
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _compute_entry_hash(
    prev_hash: str, contract_name: str, version: str, status: str,
    owner: str, owner_email: Optional[str], owner_team: Optional[str],
    asset_id: Optional[str], description: str,
    downstream_consumers: list,
    rules, contexts,
    opendqv_node_id: str, updated_at: str,
    strict_schema: bool = False, allowed_fields: list | None = None,
) -> str:
    """SHA-256 over the v2 canonical payload for a history entry.

    Hashes prev_hash, all content fields, plus node_id and updated_at — so
    the entry_hash uniquely identifies the audit event, including its
    position in the chain and when/where it was recorded.
    """
    parts = [prev_hash]
    parts.extend(_content_payload_parts(
        contract_name, version, status, owner, owner_email, owner_team,
        asset_id, description, downstream_consumers, rules, contexts,
        strict_schema=strict_schema, allowed_fields=allowed_fields,
    ))
    parts.append(_canonical_json(opendqv_node_id))
    parts.append(_canonical_json(updated_at))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class DataContract(BaseModel):
    """A versioned set of data quality rules for a business entity."""
    name: str
    version: str = "1.0"
    description: str = ""
    owner: str = ""
    status: ContractStatus = ContractStatus.ACTIVE
    rules: list[Rule] = []
    contexts: dict = {}  # context_name -> list of override rule dicts

    # CRT180 — strict_schema: reject records carrying fields the contract does
    # not declare (JSON Schema's `additionalProperties: false`, enforced at the
    # write boundary). Declared = every field a rule references (including
    # cross-field references) plus `allowed_fields`, the allow-list for extra
    # fields a strict contract accepts without a rule on them. Default off —
    # existing contracts are unaffected. Entries are validated with the
    # SEC-004 field-name charset (they become SQL identifiers downstream).
    strict_schema: bool = False
    allowed_fields: list[str] = []

    @field_validator("allowed_fields")
    @classmethod
    def _allowed_fields_safe(cls, v: list[str]) -> list[str]:
        from opendqv.core.rule_parser import _UNSAFE_FIELD_CHARS
        for name in v or []:
            if not isinstance(name, str) or not name or _UNSAFE_FIELD_CHARS.search(name):
                raise ValueError(
                    f"allowed_fields entry {name!r} is not a safe field name "
                    "(double-quote, backslash, semicolon or control characters are not permitted)."
                )
        return list(v or [])
    asset_id: Optional[str] = None  # catalog asset identifier (e.g. Collibra, Atlan, DataHub)
    downstream_consumers: list[str] = []  # Marmot MRNs of downstream consumers
    catalog_visible: bool = True  # Set False to hide from Marmot discover_data

    # sensitive_fields — list of field names whose values must never appear in logs,
    # error responses, /explain output, or ContractHistory diffs.
    # Declaring or modifying sensitive_fields requires a REVIEW cycle.
    sensitive_fields: list[str] = []

    # validate_in_states — which contract statuses allow validation against this contract.
    # Default: [ACTIVE]. Set to [DRAFT, ACTIVE] for testing. [ACTIVE] for regulatory mode.
    validate_in_states: list[str] = ["active"]

    # last_active_snapshot — captured when a contract transitions from ACTIVE to DRAFT.
    # Used by STRICT_DRAFT_VALIDATION mode to serve the last-known-good ruleset.
    last_active_snapshot: Optional[list] = None
    # CRT180 review B3: the snapshot's own strict-schema settings, captured
    # with it. The draft fallback must serve the ACTIVE contract's semantics,
    # not whatever the unapproved draft has flipped strict_schema to.
    last_active_strict_schema: bool | None = None
    last_active_fields: list | None = None

    owner_team: Optional[str] = None    # ACT-038-06: team identifier for BCBS 239 audit
    owner_email: Optional[str] = None   # ACT-038-06: contact email

    # source — immutable creation-path identifier set by the server, never by the client.
    # Values: "mcp" (agent via MCP tool), "wizard" (onboarding UI), "manual" (direct REST/YAML).
    # None for contracts loaded from pre-existing YAML files without this field.
    source: Optional[str] = None

    # REVIEW lifecycle metadata — populated when contract is in REVIEW state
    proposed_by: Optional[str] = None
    proposed_at: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    approved_by: Optional[str] = None  # also stored in ContractHistory
    approved_at: Optional[str] = None
    rejected_by: Optional[str] = None
    rejected_at: Optional[str] = None
    rejection_reason: Optional[str] = None

    model_config = {"populate_by_name": True}


class SnapshotRuleError(ValueError):
    """A historical snapshot carries a rule this engine refuses to construct
    (2.8.0: an unknown rule type recorded by an older release). Raised by
    ``contract_by_hash`` / ``contract_as_of``; the REST layer maps it to 409
    and MCP to an error envelope — never a 500, never a silent pass."""


def _contract_from_snapshot(name: str, snap: dict) -> "DataContract":
    """Rebuild a DataContract from a chain entry snapshot.

    Restores every field that's covered by the v2 hash domain, so a contract
    retrieved by hash round-trips byte-for-byte against the live YAML state
    that produced the hash. CRT169 root cause: pre-v2.3.0 reconstruction
    only restored name/version/description/owner/status/rules/contexts —
    so even after the hash domain is fixed, reconstruction had to be too.

    v2.3.20 P1.3 (Persona B 2026-04-27): also attaches the snapshot's
    own entry_hash and content_hash to the rebuilt contract object via
    private attributes ``_snap_entry_hash`` and ``_snap_content_hash``.
    Routes that received this contract via ``contract_by_hash`` can then
    echo the PINNED hash on the validate response instead of looking up
    the latest hash via ``_get_contract_hash`` (which returns the live
    head, not the pinned version). Closes the audit-replay misleading-
    metadata bug the reviewer named: pinned validation reported latest
    hashes, only ``effective_rule_hash`` reflected what actually ran.
    """
    try:
        rules = [Rule(**r) for r in snap["rules"]]
    except ValueError as exc:
        raise SnapshotRuleError(
            f"Historical snapshot of contract '{name}' (version {snap.get('version')}) carries a rule "
            f"this engine refuses to load: {exc}. The snapshot is preserved in history but cannot be "
            f"replayed by this engine version."
        ) from None
    contract = DataContract(
        name=name,
        version=snap["version"],
        description=snap.get("description") or "",
        owner=snap.get("owner") or "",
        owner_email=snap.get("owner_email"),
        owner_team=snap.get("owner_team"),
        asset_id=snap.get("asset_id"),
        downstream_consumers=snap.get("downstream_consumers") or [],
        status=snap["status"],
        rules=rules,
        contexts=snap.get("contexts") or {},
        strict_schema=bool(snap.get("strict_schema", False)),
        allowed_fields=list(snap.get("allowed_fields") or []),
    )
    # Attach the snapshot's own hashes for the validate-response echo.
    object.__setattr__(contract, "_snap_entry_hash", snap.get("entry_hash"))
    object.__setattr__(contract, "_snap_content_hash", snap.get("content_hash"))
    return contract


class ContractHistory(ContractHistoryBackend):
    """Tracks version history for contracts, persisted in SQLite."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            import opendqv.config as config
            db_path = config.DB_PATH
        self.db_path = db_path
        # For :memory: DBs keep a single persistent connection so the schema survives
        self._mem_conn = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Return a connection — shared for :memory:, new for file-based DBs."""
        if self._mem_conn is not None:
            return self._mem_conn
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        """Create the contract_history table if it doesn't exist."""
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS contract_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "contract_name TEXT NOT NULL, "
            "version TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "description TEXT, "
            "owner TEXT, "
            "owner_email TEXT, "
            "owner_team TEXT, "
            "asset_id TEXT, "
            "downstream_consumers TEXT, "
            "rules TEXT, "
            "contexts TEXT, "
            "opendqv_node_id TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "prev_hash TEXT NOT NULL DEFAULT '', "
            "entry_hash TEXT NOT NULL DEFAULT '', "
            "content_hash TEXT NOT NULL DEFAULT '', "
            "domain_version INTEGER NOT NULL DEFAULT 1, "
            "approved_by TEXT)"
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contract_history_name ON contract_history(contract_name)")
        # Migrate existing DBs that predate hash-chain columns
        for col in ("prev_hash", "entry_hash"):
            try:
                conn.execute(f"ALTER TABLE contract_history ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # column already exists
        # Migrate: approved_by column (nullable — populated when contract enters REVIEW → ACTIVE lifecycle)
        try:
            conn.execute("ALTER TABLE contract_history ADD COLUMN approved_by TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Migrate: REVIEW lifecycle and sensitive_fields columns
        for col_def in (
            "proposed_by TEXT",
            "proposed_at TEXT",
            "reviewed_by TEXT",
            "reviewed_at TEXT",
            "rejected_by TEXT",
            "rejected_at TEXT",
            "rejection_reason TEXT",
            "sensitive_fields TEXT",
            # v2.3.20 P1.4: approved_at parallel to approved_by, so
            # bundled-template attestation and live-workflow attestation
            # both have a timestamp on the history row.
            "approved_at TEXT",
        ):
            try:
                conn.execute(f"ALTER TABLE contract_history ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        # Migrate: v2.3.0 hash-domain expansion (CRT169) — content fields and content_hash
        for col_def in (
            "owner_email TEXT",
            "owner_team TEXT",
            "asset_id TEXT",
            "downstream_consumers TEXT",
            "content_hash TEXT NOT NULL DEFAULT ''",
            "domain_version INTEGER NOT NULL DEFAULT 1",
        ):
            try:
                conn.execute(f"ALTER TABLE contract_history ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        # CRT180: strict-schema flag + declared-field allow-list (nullable /
        # defaulted so pre-CRT180 rows read back as non-strict).
        for col_def in ("strict_schema INTEGER NOT NULL DEFAULT 0", "allowed_fields TEXT"):
            try:
                conn.execute(f"ALTER TABLE contract_history ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass
        # Scrub-and-restart: any pre-v2 chain entries are dev artefacts and are
        # discarded on first boot under v2.3.0. The next reload() will write
        # fresh genesis entries under the v2 hash domain. Idempotent — second
        # boot finds no rows with domain_version != 2 and is a no-op.
        self._scrub_pre_v2_entries(conn)
        conn.commit()
        if self._mem_conn is None:
            conn.close()

    def _scrub_pre_v2_entries(self, conn) -> None:
        """Truncate any chain entries minted under a pre-v2 hash domain.

        Idempotent: runs on every backend init. First boot post-upgrade finds
        legacy rows (domain_version = 1, the column default for ALTERed rows
        that predate this migration) and clears them. Second boot finds 0
        such rows and no-ops.
        """
        cur = conn.execute(
            "SELECT COUNT(*) FROM contract_history WHERE domain_version != ?",
            (_HASH_DOMAIN_VERSION,),
        )
        count = cur.fetchone()[0]
        if count > 0:
            logger.info(
                "Scrubbing %d pre-v%d chain entries from contract_history",
                count, _HASH_DOMAIN_VERSION,
            )
            conn.execute(
                "DELETE FROM contract_history WHERE domain_version != ?",
                (_HASH_DOMAIN_VERSION,),
            )

    def record_version(self, contract: DataContract, approved_by: Optional[str] = None):
        """Snapshot the current state of a contract.

        approved_by — identity of the approver when a contract transitions from
        REVIEW to ACTIVE (maker-checker). Stored for audit purposes.
        """
        rules = [
            r.model_dump(by_alias=True, exclude_none=True)
            for r in contract.rules
        ]
        contexts = copy.deepcopy(contract.contexts)
        updated_at = datetime.now(timezone.utc).isoformat()

        rules_json = json.dumps(rules, sort_keys=True)
        contexts_json = json.dumps(contexts, sort_keys=True)
        downstream_consumers = list(contract.downstream_consumers or [])
        downstream_json = json.dumps(downstream_consumers, sort_keys=True)
        fields_json = json.dumps(sorted(contract.allowed_fields or []))  # CRT180

        # Don't record duplicate consecutive snapshots for the same version
        # unless something actually changed. Compare-tuple covers every field
        # in the v2 hash domain — extending the hash domain without extending
        # this comparison would silently skip metadata-only edits and leave
        # the chain pointing at a stale snapshot (CRT169 root cause).
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            row = conn.execute(
                "SELECT version, status, description, owner, owner_email, "
                "owner_team, asset_id, downstream_consumers, rules, contexts, "
                "strict_schema, allowed_fields, entry_hash "
                "FROM contract_history WHERE contract_name = ? ORDER BY id DESC LIMIT 1",
                (contract.name,),
            ).fetchone()

            prev_hash = _GENESIS_HASH
            if row:
                (last_version, last_status, last_desc, last_owner,
                 last_owner_email, last_owner_team, last_asset_id,
                 last_downstream, last_rules, last_contexts,
                 last_strict, last_declared_fields, last_entry_hash) = row

                # v2.3.20-fix (inside-view caught regression): also
                # compare attestation fields. Without this, after a
                # bundled YAML adds proposed_by/approved_by, dedupe sees
                # no material change in the hash domain and skips the
                # row — leaving list_versions stuck on the prior
                # null-attestation row. The attestation IS material to
                # change-control regimes; treat it as a comparison
                # field. Read the current row's attestation to compare.
                cur_attestation_row = conn.execute(
                    "SELECT proposed_by, approved_by FROM contract_history "
                    "WHERE contract_name = ? ORDER BY id DESC LIMIT 1",
                    (contract.name,),
                ).fetchone()
                last_proposed_by = cur_attestation_row[0] if cur_attestation_row else None
                last_approved_by = cur_attestation_row[1] if cur_attestation_row else None

                if (last_version == contract.version
                        and last_status == contract.status.value
                        and last_rules == rules_json
                        and last_contexts == contexts_json
                        and last_desc == contract.description
                        and last_owner == contract.owner
                        and last_owner_email == contract.owner_email
                        and last_owner_team == contract.owner_team
                        and last_asset_id == contract.asset_id
                        and (last_downstream or "[]") == downstream_json
                        and bool(last_strict) == bool(contract.strict_schema)
                        and (last_declared_fields or "[]") == fields_json
                        and last_proposed_by == contract.proposed_by
                        and last_approved_by == (
                            approved_by if approved_by is not None
                            else contract.approved_by
                        )):
                    return
                prev_hash = last_entry_hash or _GENESIS_HASH

            entry_hash = _compute_entry_hash(
                prev_hash, contract.name, contract.version, contract.status.value,
                contract.owner, contract.owner_email, contract.owner_team,
                contract.asset_id, contract.description, downstream_consumers,
                rules, contexts,
                config.OPENDQV_NODE_ID, updated_at,
                strict_schema=contract.strict_schema, allowed_fields=contract.allowed_fields,
            )
            content_hash = _compute_content_hash(
                contract.name, contract.version, contract.status.value,
                contract.owner, contract.owner_email, contract.owner_team,
                contract.asset_id, contract.description, downstream_consumers,
                rules, contexts,
                strict_schema=contract.strict_schema, allowed_fields=contract.allowed_fields,
            )

            # v2.3.17 F-C: at-most-one-active invariant. Before inserting an
            # ACTIVE row for (contract_name, version), demote any prior ACTIVE
            # rows for the same (name, version) to ARCHIVED. The history table
            # is append-only for chain integrity, but the *status field* on
            # historical rows is a state attribute and SHOULD be updated when
            # the truth about that row changes (it is no longer the active one).
            # Without this, list_versions returns multiple status:active rows
            # for the same version — Persona B's F-C finding. The ContractStatus
            # state machine permits ACTIVE → ARCHIVED.
            if contract.status == ContractStatus.ACTIVE:
                conn.execute(
                    "UPDATE contract_history SET status = ? "
                    "WHERE contract_name = ? AND version = ? AND status = ?",
                    (ContractStatus.ARCHIVED.value, contract.name,
                     contract.version, ContractStatus.ACTIVE.value),
                )

            # v2.3.20 P1.4: when no explicit approved_by is passed (e.g.
            # YAML-loaded bundled exemplars), fall back to the contract
            # object's own approved_by attribute. Same fallback for
            # proposed_by + proposed_at. Lets bundled YAMLs declare
            # template-level attestation that flows through to history
            # (and therefore to list_versions). Customer-forked workflow
            # callers continue to override via the explicit approved_by
            # param on registry.approve_contract.
            _eff_approved_by = approved_by if approved_by is not None else contract.approved_by
            _eff_approved_at = contract.approved_at
            _eff_proposed_by = contract.proposed_by
            _eff_proposed_at = contract.proposed_at

            conn.execute(
                "INSERT INTO contract_history "
                "(contract_name, version, status, description, owner, "
                " owner_email, owner_team, asset_id, downstream_consumers, "
                " rules, contexts, opendqv_node_id, updated_at, "
                " prev_hash, entry_hash, content_hash, domain_version, "
                " approved_by, approved_at, proposed_by, proposed_at, "
                " strict_schema, allowed_fields) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (contract.name, contract.version, contract.status.value,
                 contract.description, contract.owner,
                 contract.owner_email, contract.owner_team, contract.asset_id,
                 downstream_json, rules_json, contexts_json,
                 config.OPENDQV_NODE_ID, updated_at,
                 prev_hash, entry_hash, content_hash, _HASH_DOMAIN_VERSION,
                 _eff_approved_by, _eff_approved_at,
                 _eff_proposed_by, _eff_proposed_at,
                 1 if contract.strict_schema else 0, fields_json),
            )
            conn.commit()
        finally:
            if not is_shared:
                conn.close()

    def get_as_of(self, contract_name: str, timestamp: str) -> Optional[dict]:
        """
        Return the most recent history snapshot with updated_at <= timestamp.

        Used by the ?as_of= validate query parameter for point-in-time validation —
        required for EMA clinical trial submissions, MiFIR regulatory reporting,
        and insurance claim dispute resolution.

        timestamp: ISO 8601 string (e.g. "2026-06-01T00:00:00Z")
        Returns the snapshot dict, or None if no snapshot existed at that time.
        """
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            row = conn.execute(
                "SELECT version, status, description, owner, "
                "owner_email, owner_team, asset_id, downstream_consumers, "
                "rules, contexts, opendqv_node_id, updated_at, "
                "strict_schema, allowed_fields "
                "FROM contract_history "
                "WHERE contract_name = ? AND updated_at <= ? "
                "ORDER BY id DESC LIMIT 1",
                (contract_name, timestamp),
            ).fetchone()
        finally:
            if not is_shared:
                conn.close()

        if not row:
            return None
        (version, status, description, owner, owner_email, owner_team,
         asset_id, downstream_json, rules_json, contexts_json,
         opendqv_node_id, updated_at, strict_schema, declared_fields_json) = row
        return {
            "version": version,
            "status": status,
            "description": description or "",
            "owner": owner or "",
            "owner_email": owner_email,
            "owner_team": owner_team,
            "asset_id": asset_id,
            "downstream_consumers": json.loads(downstream_json) if downstream_json else [],
            "rules": json.loads(rules_json),
            "contexts": json.loads(contexts_json),
            "strict_schema": bool(strict_schema),
            "allowed_fields": json.loads(declared_fields_json) if declared_fields_json else [],
            "opendqv_node_id": opendqv_node_id,
            "updated_at": updated_at,
        }

    def get_history(self, contract_name: str) -> list[dict]:
        """Get version history for a contract."""
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute(
                "SELECT version, status, description, owner, "
                "owner_email, owner_team, asset_id, downstream_consumers, "
                "rules, contexts, opendqv_node_id, updated_at, "
                "prev_hash, entry_hash, content_hash, domain_version, approved_by, "
                "approved_at, "
                "proposed_by, proposed_at, rejected_by, rejected_at, rejection_reason, "
                "strict_schema, allowed_fields "
                "FROM contract_history WHERE contract_name = ? ORDER BY id",
                (contract_name,),
            ).fetchall()
        finally:
            if not is_shared:
                conn.close()

        history = []
        for (version, status, description, owner, owner_email, owner_team,
             asset_id, downstream_json, rules_json, contexts_json,
             opendqv_node_id, updated_at, prev_hash, entry_hash, content_hash,
             domain_version, approved_by, approved_at, proposed_by, proposed_at,
             rejected_by, rejected_at, rejection_reason,
             strict_schema, declared_fields_json) in rows:
            history.append({
                "version": version,
                "status": status,
                "description": description,
                "owner": owner,
                "owner_email": owner_email,
                "owner_team": owner_team,
                "asset_id": asset_id,
                "downstream_consumers": json.loads(downstream_json) if downstream_json else [],
                "rules": json.loads(rules_json),
                "contexts": json.loads(contexts_json),
                "opendqv_node_id": opendqv_node_id,
                "updated_at": updated_at,
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
                "content_hash": content_hash,
                "domain_version": domain_version,
                "approved_by": approved_by,
                "approved_at": approved_at,
                "proposed_by": proposed_by,
                "proposed_at": proposed_at,
                "rejected_by": rejected_by,
                "rejected_at": rejected_at,
                "rejection_reason": rejection_reason,
                "strict_schema": bool(strict_schema),
                "allowed_fields": json.loads(declared_fields_json) if declared_fields_json else [],
            })
        return history

    def diff(self, contract_name: str, version_a: str, version_b: str) -> dict:
        """Compare two versions of a contract. Returns added/removed/changed rules."""
        history = self.get_history(contract_name)

        snap_a = None
        snap_b = None
        for snap in history:
            if snap["version"] == version_a and snap_a is None:
                snap_a = snap
            if snap["version"] == version_b:
                snap_b = snap  # take the latest snapshot for this version

        if not snap_a:
            raise ValueError(f"Version '{version_a}' not found in history for '{contract_name}'")
        if not snap_b:
            raise ValueError(f"Version '{version_b}' not found in history for '{contract_name}'")

        return self._diff_snaps(contract_name, snap_a, snap_b, version_a, version_b)

    def diff_by_hash(self, contract_name: str, hash_a: str, hash_b: str) -> dict:
        """Compare two snapshots of a contract identified by entry_hash or content_hash."""
        history = self.get_history(contract_name)

        def _find(h: str) -> Optional[dict]:
            for snap in history:
                if snap.get("entry_hash") == h or snap.get("content_hash") == h:
                    return snap
            return None

        snap_a = _find(hash_a)
        snap_b = _find(hash_b)

        if not snap_a:
            raise ValueError(f"Hash '{hash_a}' not found in history for '{contract_name}'")
        if not snap_b:
            raise ValueError(f"Hash '{hash_b}' not found in history for '{contract_name}'")

        result = self._diff_snaps(contract_name, snap_a, snap_b, snap_a["version"], snap_b["version"])
        result["from_hash"] = hash_a
        result["to_hash"] = hash_b
        return result

    def _diff_snaps(self, contract_name: str, snap_a: dict, snap_b: dict,
                    version_a: str, version_b: str) -> dict:
        # Index rules by name for comparison
        rules_a = {r["name"]: r for r in snap_a["rules"]}
        rules_b = {r["name"]: r for r in snap_b["rules"]}

        names_a = set(rules_a.keys())
        names_b = set(rules_b.keys())

        rules_added = []
        for name in sorted(names_b - names_a):
            r = rules_b[name]
            rules_added.append({"name": name, "type": r.get("type", ""), "field": r.get("field", "")})

        rules_removed = []
        for name in sorted(names_a - names_b):
            r = rules_a[name]
            rules_removed.append({"name": name, "type": r.get("type", ""), "field": r.get("field", "")})

        rules_changed = []
        for name in sorted(names_a & names_b):
            ra = rules_a[name]
            rb = rules_b[name]
            if ra != rb:
                changes = {}
                all_keys = set(ra.keys()) | set(rb.keys())
                for key in sorted(all_keys):
                    old_val = ra.get(key)
                    new_val = rb.get(key)
                    if old_val != new_val:
                        changes[key] = {"old": old_val, "new": new_val}
                rules_changed.append({
                    "name": name,
                    "field": rb.get("field", ra.get("field", "")),
                    "changes": changes,
                })

        metadata_changed = {}
        for key in ("status", "description", "owner"):
            if snap_a.get(key) != snap_b.get(key):
                metadata_changed[key] = {"old": snap_a.get(key), "new": snap_b.get(key)}

        return {
            "contract": contract_name,
            "from_version": version_a,
            "to_version": version_b,
            "changes": {
                "rules_added": rules_added,
                "rules_removed": rules_removed,
                "rules_changed": rules_changed,
                "metadata_changed": metadata_changed,
            },
        }


def _version_sort_key(version: str) -> tuple:
    """Total ordering key for a contract version string, used to resolve "latest".

    Handles both plain versions ("1.0", "2.3.1") and draft-suffixed versions
    produced by _bump_draft_patch_counter ("1.0-draft.2"). The previous key
    collapsed *any* version containing a non-digit — every "-draft.N" — to a
    constant, so once draft patch-counters existed "latest" resolution became
    order-dependent (CRT175 #4).

    Ordering rules, ascending:
      1. numeric base parts compared left-to-right ("2.0" > "1.9")
      2. a released version outranks its own drafts ("1.0" > "1.0-draft.5")
      3. among drafts of the same base, higher counter is later
    So sorted(...)[-1] is the most-authoritative/newest version.
    """
    base, _, draft = version.partition("-draft.")
    parts = []
    for p in base.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)  # non-numeric segment — treat as 0, keep total order
    if draft == "":
        # Released version: outranks its drafts (released_flag=1, counter=0)
        released_flag, draft_n = 1, 0
    else:
        released_flag = 0
        try:
            draft_n = int(draft)
        except ValueError:
            draft_n = 0
    # Trailing raw-string component guarantees a TOTAL order: two distinct
    # version strings that coerce to the same numeric key (e.g. a malformed
    # hand-typed "1.0-draft" vs the released "1.0") can no longer tie and fall
    # back to nondeterministic dict-iteration order (Sonnet red-team, CRT175).
    return (tuple(parts), released_flag, draft_n, version)


def validate_promotion_readiness(contract: "DataContract") -> list[str]:
    """Return list of missing fields blocking promotion to ACTIVE. Empty = ready."""
    issues = []
    if not (contract.description or "").strip():
        issues.append("description is required and must not be empty")
    if not (contract.owner or "").strip():
        issues.append("owner is required and must not be empty")
    if not contract.rules:
        issues.append("at least one rule must be defined")
    return issues


class ContractRegistry:
    """
    Loads and manages data contracts from a directory of YAML files.

    Contracts are cached in memory. Call reload() to refresh from disk.
    """

    def __init__(self, contracts_dir: Path):
        self.contracts_dir = contracts_dir
        self._contracts: dict[str, dict[str, DataContract]] = {}  # name -> {version -> contract}
        # name -> {version -> source YAML path}. Mirrors _contracts (CRT178:
        # a name-only index stamped one version's approval into another
        # version's file when a contract was stored one-file-per-version).
        self._contract_paths: dict[str, dict[str, Path]] = {}
        import opendqv.config as config
        self.history = ContractHistory(config.DB_PATH)
        self.reload()

    def reload(self):
        """Load/reload all contracts from the contracts directory.

        A file that fails to load is logged and recorded in ``load_failures``
        (``[{"file", "error"}]``) so ``POST /contracts/reload`` can report it;
        the other contracts load. A ``contexts:`` override is constructed
        here too (2.8.0), so an override naming an unknown rule type refuses
        the whole file at load rather than failing every request that names
        the context."""
        self._contracts = {}
        self._contract_paths = {}
        self.load_failures: list[dict] = []
        if not self.contracts_dir.exists():
            logger.warning("Contracts directory not found: %s", self.contracts_dir)
            return

        for path in sorted(self.contracts_dir.glob("*.yaml")):
            try:
                contract = self._load_file(path)
                if contract:
                    self._check_contexts(contract)
                    if contract.name not in self._contracts:
                        self._contracts[contract.name] = {}
                    self._contracts[contract.name][contract.version] = contract
                    self._contract_paths.setdefault(contract.name, {})[contract.version] = path
                    self.history.record_version(contract)
                    logger.info("Loaded contract: %s v%s (%d rules)",
                                contract.name, contract.version, len(contract.rules))
            except Exception as e:
                logger.error("Failed to load contract from %s: %s", path.name, e)
                self.load_failures.append({"file": path.name, "error": str(e)})

    def _check_contexts(self, contract: "DataContract") -> None:
        """Construct every context's merged rule set once at load so an
        override that the Rule model refuses (unknown type, bad pattern) is a
        load error naming the context — not a per-request failure."""
        for ctx in (contract.contexts or {}):
            try:
                self.get_rules_with_context(contract, ctx)
            except ValueError as exc:
                raise ValueError(f"context '{ctx}': {exc}") from None

    def _load_file(self, path: Path) -> Optional[DataContract]:
        """Parse a single contract YAML file."""
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            # Surface line/column so authors can find and fix syntax errors quickly.
            mark = getattr(exc, "problem_mark", None)
            location = (
                f" (line {mark.line + 1}, column {mark.column + 1})"
                if mark else ""
            )
            problem = getattr(exc, "problem", None) or str(exc)
            raise ValueError(
                f"Contract file found but failed to parse{location}: {problem}"
            ) from exc
        if not raw:
            return None

        # Support two formats:
        # 1. Contract format: has 'contract' top-level key
        # 2. Legacy format: has 'rules' as a list (like starter-rules.yaml)
        if "contract" in raw:
            return self._parse_contract_format(raw)
        elif "rules" in raw and isinstance(raw["rules"], list):
            return self._parse_legacy_format(raw, path)
        elif "rules" in raw and isinstance(raw["rules"], dict):
            return self._parse_onboarding_format(raw, path)
        return None

    def _parse_contract_format(self, raw: dict) -> DataContract:
        """Parse the canonical contract format."""
        c = raw["contract"]
        rules = [Rule(**r) for r in c.get("rules", [])]
        return DataContract(
            name=c["name"],
            version=str(c.get("version", "1.0")),
            description=c.get("description", ""),
            owner=c.get("owner", ""),
            status=c.get("status", "active"),
            rules=rules,
            contexts=c.get("contexts", {}),
            strict_schema=bool(c.get("strict_schema", False)),
            # `fields:` accepted as a deprecated alias (review S3 rename); the linter warns.
            allowed_fields=list(c.get("allowed_fields") or c.get("fields") or []),
            asset_id=c.get("asset_id"),
            downstream_consumers=c.get("downstream_consumers", []),
            catalog_visible=c.get("catalog_visible", True),
            sensitive_fields=c.get("sensitive_fields", []),
            validate_in_states=c.get("validate_in_states", ["active"]),
            owner_team=c.get("owner_team"),
            owner_email=c.get("owner_email"),
            source=c.get("source"),
            proposed_by=c.get("proposed_by"),
            proposed_at=c.get("proposed_at"),
            # v2.3.20 P1.4: also read template-level approved_by / approved_at
            # from the YAML so bundled exemplar contracts can carry their
            # OpenDQV-core-team attestation through to list_versions.
            # Pilot's "they are templates" framing: the OpenDQV core team
            # genuinely did propose + review these template versions; that
            # IS honest attestation, not synthetic. Customer forks add
            # THEIR organization's attestation when they go through their
            # own approve workflow.
            approved_by=c.get("approved_by"),
            approved_at=c.get("approved_at"),
            # CRT177 Tier 2: rejection metadata is now serialised by
            # reject_contract, so it must be read back or it is lost on reload.
            # Keep this set in sync with _PERSISTED_META_FIELDS.
            rejected_by=c.get("rejected_by"),
            rejected_at=c.get("rejected_at"),
            rejection_reason=c.get("rejection_reason"),
        )

    def _parse_legacy_format(self, raw: dict, path: Path) -> DataContract:
        """Parse flat rules list format (like starter-rules.yaml)."""
        rules = [Rule(**r) for r in raw["rules"]]
        name = path.stem.replace("-", "_").replace(" ", "_")
        return DataContract(
            name=name,
            version=str(raw.get("version", "1.0")),
            description=raw.get("description", ""),
            rules=rules,
            contexts=raw.get("contexts", {}),
            # CRT177 Tier 2: this format has no `contract:` block, so lifecycle
            # status and provenance are persisted at the top level. Read them
            # back or a transition is silently lost on reload.
            **_read_meta(raw),
        )

    def _parse_onboarding_format(self, raw: dict, path: Path) -> DataContract:
        """Parse the field-keyed rules format (like customer_onboarding.yaml)."""
        rules = []
        for field_name, field_def in raw["rules"].items():
            rule_dict = {
                "name": f"validate_{field_name}",
                "field": field_name,
                "type": field_def.get("type", "not_empty"),
                "description": field_def.get("error_message", ""),
                "error_message": field_def.get("error_message", f"Invalid {field_name}"),
                "severity": field_def.get("severity", "error"),
            }
            if "regex" in field_def:
                rule_dict["type"] = "regex"
                rule_dict["pattern"] = field_def["regex"]
            if "min_length" in field_def:
                rule_dict["min_length"] = field_def["min_length"]
            if "max_length" in field_def:
                rule_dict["max_length"] = field_def["max_length"]
            if "min" in field_def:
                # Only set numeric min — date strings like "1900-01-01" are not numeric rules
                try:
                    rule_dict["min"] = float(field_def["min"])
                except (ValueError, TypeError):
                    pass  # date-based min — handled by date_format rule type
            if "max" in field_def:
                try:
                    if field_def["max"] != "today":
                        rule_dict["max"] = float(field_def["max"])
                except (ValueError, TypeError):
                    pass
            if field_def.get("required"):
                # Add a separate not_empty rule for required fields
                rules.append(Rule(
                    name=f"{field_name}_required",
                    field=field_name,
                    type="not_empty",
                    severity=Severity.ERROR,
                    error_message=f"{field_name} is required",
                ))
            if "format" in field_def and field_def.get("type") == "date":
                rule_dict["type"] = "date_format"
                rule_dict["format"] = field_def["format"]
            if rule_dict["type"] not in RULE_TYPES:
                # In this format `type:` is a DATA type (string, number, date,
                # email…), not a rule type. Until 2.7.0 it reached the engine
                # as an unknown rule type and passed silently; with the closed
                # set (2.8.0) it would refuse the whole file. Emit one rule per
                # enforceable constraint instead, and warn when there is none.
                dtype = rule_dict.pop("type")
                base = {k: v for k, v in rule_dict.items() if k not in ("min_length", "max_length", "min", "max")}
                emitted = False
                for key in ("min_length", "max_length", "min", "max"):
                    if key in rule_dict:
                        rules.append(Rule(**{**base, "type": key, key: rule_dict[key], "name": f"{base['name']}_{key}"}))
                        emitted = True
                if not emitted:
                    logger.warning("%s: field '%s' declares data type '%s' with no enforceable constraint — no rule emitted",
                                   path.name, field_name, dtype)
                continue
            rules.append(Rule(**rule_dict))

        name = path.stem.replace("-", "_").replace(" ", "_")
        metadata = raw.get("metadata", {})
        return DataContract(
            name=name,
            version=str(metadata.get("version", "1.0")),
            description=metadata.get("description", ""),
            owner=metadata.get("author", ""),
            rules=rules,
            contexts=raw.get("contexts", {}),
            # CRT177 Tier 2: lifecycle status/provenance are persisted at the
            # top level for this format too (no `contract:` block).
            **_read_meta(raw),
        )

    def get(self, name: str, version: str = "latest") -> Optional[DataContract]:
        """Return a contract by name and version.

        Args:
            name: Contract name as defined in the YAML file.
            version: Semantic version string (e.g. "1.0.0") or "latest" to
                     resolve the highest available version automatically.

        Returns:
            The matching DataContract, or None if the name or version is not found.
        """
        versions = self._contracts.get(name)
        if not versions:
            return None
        if version == "latest":
            latest_key = sorted(versions.keys(), key=_version_sort_key)[-1]
            return versions[latest_key]
        return versions.get(version)

    def list_contracts(self, include_all: bool = False) -> list[dict]:
        """List contracts.

        Default (`include_all=False`) returns ALL non-archived contracts:
        status in {active, draft, review}. Set `include_all=True` to
        also include ARCHIVED entries.

        v2.3.23 P1-10: docstring previously said "By default only ACTIVE
        contracts" — that was wrong. Behaviour was always non-archived.
        Persona B reported the resulting "off-by-two" confusion against
        `governance.active_count` from /api/v1/stats. Both numbers are
        correct; the difference is the draft/review entries that
        list_contracts surfaces but the active counter excludes.
        Consumers needing active-only should filter on `status ==
        'active'` or read `governance.active_count` directly.
        """
        result = []
        for name, versions in sorted(self._contracts.items()):
            for ver, contract in sorted(versions.items()):
                if not include_all and contract.status == ContractStatus.ARCHIVED:
                    continue
                result.append({
                    "name": contract.name,
                    "version": contract.version,
                    "description": contract.description,
                    "owner": contract.owner,
                    "status": contract.status.value,
                    "rule_count": len(contract.rules),
                    "asset_id": contract.asset_id,
                })
        return result

    # Valid transitions for set_status(). Archived contracts must re-enter the
    # lifecycle via DRAFT — jumping from ARCHIVED directly to ACTIVE or REVIEW
    # would bypass the maker-checker review workflow entirely.
    _VALID_TRANSITIONS: dict = {
        ContractStatus.DRAFT:     {ContractStatus.ACTIVE, ContractStatus.ARCHIVED},
        ContractStatus.REVIEW:    {ContractStatus.ACTIVE, ContractStatus.DRAFT, ContractStatus.ARCHIVED},
        ContractStatus.ACTIVE:    {ContractStatus.ARCHIVED, ContractStatus.DRAFT},
        ContractStatus.ARCHIVED:  {ContractStatus.DRAFT},
    }

    def set_status(self, name: str, version: str, status: ContractStatus) -> Optional[DataContract]:
        """Change a contract's lifecycle status and persist it to the source YAML file."""
        contract = self.get(name, version)
        if not contract:
            return None
        allowed = self._VALID_TRANSITIONS.get(contract.status, set())
        if status not in allowed:
            raise ValueError(
                f"Invalid transition: {contract.status.value} → {status.value}. "
                f"Allowed from {contract.status.value}: "
                f"{', '.join(s.value for s in sorted(allowed, key=lambda s: s.value)) or 'none'}."
            )
        # Capture snapshot when transitioning TO draft from active
        if status == ContractStatus.DRAFT and contract.status == ContractStatus.ACTIVE:
            contract.last_active_snapshot = copy.deepcopy([r.model_dump(by_alias=True) for r in contract.rules])
            contract.last_active_strict_schema = bool(contract.strict_schema)
            contract.last_active_fields = list(contract.allowed_fields or [])
        contract.status = status
        self.history.record_version(contract)
        logger.info("Contract %s v%s status changed to %s", name, contract.version, status.value)

        # Write the new status back to the YAML file so it survives reload.
        # CRT177 Tier 2: this used an unanchored `^( +status: )\S+` regex that
        # rewrote EVERY indented `status:` line in the file — it would corrupt a
        # nested `status` key (e.g. inside contexts: or a rule) and the write was
        # non-atomic. Replaced with structured serialisation through the shared
        # atomic writer.
        self._persist_contract_meta(name, contract)
        return contract

    def submit_for_review(self, name: str, version: str, proposed_by: str) -> Optional[DataContract]:
        """Transition contract from DRAFT to REVIEW. Returns updated contract or None."""
        contract = self.get(name, version)
        if not contract:
            return None
        if contract.status != ContractStatus.DRAFT:
            raise ValueError(f"Only DRAFT contracts can be submitted for review (current: {contract.status.value})")
        contract.status = ContractStatus.REVIEW
        contract.proposed_by = proposed_by
        contract.proposed_at = datetime.now(timezone.utc).isoformat()
        self.history.record_version(contract)
        # CRT177 Tier 2: persist — this transition previously lived only in
        # memory, so a restart/reload silently reverted it to the on-disk status.
        self._persist_contract_meta(name, contract)
        return contract

    def approve_contract(self, name: str, version: str, approved_by: str) -> Optional[DataContract]:
        """Transition contract from REVIEW to ACTIVE. Records approver."""
        contract = self.get(name, version)
        if not contract:
            return None
        if contract.status != ContractStatus.REVIEW:
            raise ValueError(f"Only REVIEW contracts can be approved (current: {contract.status.value})")
        contract.status = ContractStatus.ACTIVE
        contract.approved_by = approved_by
        contract.approved_at = datetime.now(timezone.utc).isoformat()
        self.history.record_version(contract, approved_by=approved_by)
        # CRT177 Tier 2: an approval is the single most important transition to
        # persist — without this it reverted to `draft` on the next reload while
        # history claimed ACTIVE.
        self._persist_contract_meta(name, contract)
        return contract

    def reject_contract(self, name: str, version: str, rejected_by: str, reason: str) -> Optional[DataContract]:
        """Transition contract from REVIEW back to DRAFT with rejection reason."""
        contract = self.get(name, version)
        if not contract:
            return None
        if contract.status != ContractStatus.REVIEW:
            raise ValueError(f"Only REVIEW contracts can be rejected (current: {contract.status.value})")
        contract.status = ContractStatus.DRAFT
        contract.rejected_by = rejected_by
        contract.rejected_at = datetime.now(timezone.utc).isoformat()
        contract.rejection_reason = reason
        self.history.record_version(contract)
        # CRT177 Tier 2: persist the rejection + its reason.
        self._persist_contract_meta(name, contract)
        return contract

    def create_draft(
        self,
        name: str,
        description: str,
        owner: str,
        created_by: str,
        rules_data: list[dict],
    ) -> DataContract:
        """Create a new DRAFT contract from agent-supplied parameters.

        Called by the MCP create_contract_draft tool. Always creates status=DRAFT,
        source='mcp', and sets proposed_by=created_by. The contract is written to
        a YAML file in the contracts directory and registered in memory immediately —
        it can be used for validation (draft status) but will not appear as ACTIVE
        in the shared library until a human approves it via submit_for_review +
        approve_contract.

        Raises ValueError if the name is not a valid contract name, does not
        start with 'MCP_', if the contract already exists, or if any rule
        definition is invalid.
        """
        # CRT178 #10: the prefix check alone let "MCP_../../x" through to an
        # unguarded ``contracts_dir / f"{name}.yaml"`` write — an arbitrary
        # file write from an agent-facing tool. Charset first, prefix second.
        if not isinstance(name, str) or not CONTRACT_NAME_RE.match(name):
            raise ValueError(
                f"Invalid contract name '{name}'. Names must contain only letters, "
                f"digits, hyphens, and underscores (1-100 chars)."
            )
        if not name.startswith("MCP_"):
            raise ValueError(
                f"Agent-created contracts must be named with the 'MCP_' prefix "
                f"(e.g. MCP_satellite_telemetry). Got: '{name}'"
            )

        if self.get(name):
            raise ValueError(
                f"Contract '{name}' already exists. Choose a different name or "
                f"delete the existing draft first."
            )

        rules = []
        for i, r in enumerate(rules_data):
            try:
                rules.append(Rule(**r))
            except Exception as exc:
                raise ValueError(f"Invalid rule at index {i}: {exc}") from exc

        contract = DataContract(
            name=name,
            version="1.0",
            description=description,
            owner=owner,
            status=ContractStatus.DRAFT,
            rules=rules,
            source="mcp",
            proposed_by=created_by,
            # proposed_at is intentionally NOT set here — it is set by submit_for_review.
            # The ACT-046-07 guard checks proposed_at to confirm the review workflow was followed.
            # Allow validation against this contract while in DRAFT (for testing by creator).
            validate_in_states=["draft", "active"],
        )

        path = self.contracts_dir / f"{name}.yaml"
        # Defence in depth: the charset above already forbids separators; this
        # containment check makes the invariant explicit and independent of it.
        if path.resolve().parent != self.contracts_dir.resolve():
            raise ValueError(f"Contract path escapes the contracts directory: {name}")
        path.write_text(self._contract_to_yaml(contract), encoding="utf-8")

        if name not in self._contracts:
            self._contracts[name] = {}
        self._contracts[name][contract.version] = contract
        self._contract_paths.setdefault(name, {})[contract.version] = path

        self.history.record_version(contract)
        logger.info(
            "Created DRAFT contract '%s' via MCP (proposed_by=%s, rules=%d)",
            name, created_by, len(rules),
        )
        return contract

    _VERSION_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,49}$")

    def create_version(self, name: str, from_version: str, new_version: str) -> DataContract:
        """Create ``new_version`` of ``name`` as a NEW, DRAFT contract object with its own file.

        CRT178 #2: the old route mutated the fetched ACTIVE object in place and
        stored the same object under the new key, so the live version read
        DRAFT/new_version for every concurrent /validate, and nothing reached
        disk — the next reload erased the bump. Now: deep copy, strip the
        approval trail (a still-DRAFT version must not read as approved after
        a reload), write ``{name}_v{new_version}.yaml`` atomically from the
        base file's raw YAML (lossless: rules, contexts and every other key
        carry over), and index both maps.
        """
        base = self.get(name, from_version)
        if not base:
            raise ValueError(f"Contract '{name}' v{from_version} not found")
        if not isinstance(new_version, str) or not self._VERSION_RE.match(new_version):
            raise ValueError(
                f"Invalid version '{new_version}': letters, digits, '.', '_' and '-' only (max 50 chars)."
            )
        if new_version == base.version or new_version in self._contracts.get(name, {}):
            raise ValueError(f"Contract '{name}' already has a version '{new_version}'.")

        new = copy.deepcopy(base)
        new.version = new_version
        new.status = ContractStatus.DRAFT
        for f in ("approved_by", "approved_at", "proposed_by", "proposed_at",
                  "rejected_by", "rejected_at", "rejection_reason"):
            setattr(new, f, None)

        # Build the file from the base file's raw YAML so nothing is lost.
        try:
            base_path = self._path_for(name, base.version)
        except KeyError:
            base_path = None
        raw: dict = {}
        if base_path and base_path.exists():
            raw = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
        block = raw["contract"] if isinstance(raw.get("contract"), dict) else raw
        if not block:
            block = {"name": name, "rules": [r.model_dump(by_alias=True, exclude_none=True, mode="json") for r in new.rules]}
            raw = {"contract": block}
        block["name"] = name
        block["version"] = new_version
        block["status"] = ContractStatus.DRAFT.value
        for f in ("approved_by", "approved_at", "proposed_by", "proposed_at",
                  "rejected_by", "rejected_at", "rejection_reason"):
            block.pop(f, None)

        path = self.contracts_dir / f"{name}_v{new_version}.yaml"
        if path.resolve().parent != self.contracts_dir.resolve():
            raise ValueError(f"Version path escapes the contracts directory: {new_version}")
        if path.exists():
            raise ValueError(f"A file for '{name}' v{new_version} already exists: {path.name}")
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)

        self._contracts.setdefault(name, {})[new_version] = new
        self._contract_paths.setdefault(name, {})[new_version] = path
        self.history.record_version(new)
        logger.info("create_version contract=%s from=%s new=%s status=draft file=%s", name, base.version, new_version, path.name)
        return new

    # Rule fields that are engine state, never contract content. Mirrors the
    # ODCS exporter's engine-stamped set (importers/odcs.py) so both writers
    # agree on what a contract file may carry.
    _ENGINE_STAMPED_RULE_FIELDS = frozenset({
        "inherited", "federation_tier", "provenance", "severity_floor", "lookup_auth_header",
    })
    _RULE_KEY_ORDER = ("name", "description", "type", "field", "severity", "error_message")

    @classmethod
    def _rule_to_yaml_dict(cls, r: Rule) -> dict:
        """Full, lossless rule dict for disk: every set field, YAML aliases (min/max),
        enums as strings, hot-path caches and engine-stamped fields dropped.

        #149: the previous hand-written whitelist wrote only min/max/pattern/
        min_length/max_length, so an MCP draft with a compare, lookup, condition,
        checksum or optional lost those parameters on disk.
        """
        d = r.model_dump(by_alias=True, exclude_none=True, mode="json")
        for k in list(d):
            if k in cls._ENGINE_STAMPED_RULE_FIELDS or k.startswith("cached_") or k == "compiled_pattern":
                d.pop(k, None)
        # Keep the historical shape: these three are always present.
        d.setdefault("description", r.description or "")
        d.setdefault("error_message", r.error_message or "")
        d.setdefault("negate", bool(r.negate))
        d.setdefault("all_of", r.all_of)
        ordered = {k: d[k] for k in cls._RULE_KEY_ORDER if k in d}
        ordered.update({k: v for k, v in d.items() if k not in ordered})
        return ordered

    def _contract_to_yaml(self, contract: DataContract) -> str:
        """Serialize a DataContract to canonical YAML for disk storage."""
        rules_list = [self._rule_to_yaml_dict(r) for r in contract.rules]

        data = {
            "contract": {
                "name": contract.name,
                "version": contract.version,
                "description": contract.description,
                "owner": contract.owner,
                "status": contract.status.value,
                "source": contract.source,
                "proposed_by": contract.proposed_by,
                "proposed_at": contract.proposed_at,
                "validate_in_states": contract.validate_in_states,
                "rules": rules_list,
            }
        }
        if contract.downstream_consumers:
            data["contract"]["downstream_consumers"] = contract.downstream_consumers
        if not contract.catalog_visible:
            data["contract"]["catalog_visible"] = False
        if contract.strict_schema:
            data["contract"]["strict_schema"] = True
        if contract.allowed_fields:
            data["contract"]["allowed_fields"] = list(contract.allowed_fields)
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    def contract_as_of(self, name: str, timestamp: str) -> Optional["DataContract"]:
        """
        Reconstruct the DataContract that was active at the given ISO 8601 timestamp.

        Queries the contract history for the most recent snapshot with
        updated_at <= timestamp and rebuilds a DataContract from it.

        Returns None if the contract did not exist at that time.
        Used by POST /validate?as_of=<timestamp> for point-in-time validation.
        """
        snap = self.history.get_as_of(name, timestamp)
        if not snap:
            return None
        return _contract_from_snapshot(name, snap)

    def contract_by_hash(self, name: str, contract_hash: str) -> Optional["DataContract"]:
        """
        Reconstruct the DataContract whose history entry matches the given hash.

        Used by GET /contracts/{name}?hash=<contract_hash> — callers want the
        exact contract version that produced a given entry_hash on a prior
        validation response, for regulator-grade point-in-time audit retrieval.
        Matches against either entry_hash or content_hash so callers that
        captured a content_hash from a v2.3.0+ response can replay too.
        Returns None if no history entry matches.
        """
        if not contract_hash:
            return None
        for snap in self.history.get_history(name):
            if (snap.get("entry_hash") == contract_hash
                    or snap.get("content_hash") == contract_hash):
                return _contract_from_snapshot(name, snap)
        return None

    def get_history(self, name: str) -> list[dict]:
        """Get version history for a contract."""
        return self.history.get_history(name)

    def diff_versions(self, name: str, version_a: str, version_b: str) -> dict:
        """Compare two versions of a contract."""
        return self.history.diff(name, version_a, version_b)

    def diff_by_hash(self, name: str, hash_a: str, hash_b: str) -> dict:
        """Compare two history snapshots identified by entry_hash or content_hash."""
        return self.history.diff_by_hash(name, hash_a, hash_b)

    @staticmethod
    def _bump_draft_patch_counter(version: str) -> str:
        """Increment the draft patch counter suffix on a version string.

        Examples:
            "1.0"          → "1.0-draft.1"
            "1.0-draft.1"  → "1.0-draft.2"
            "2.3-draft.9"  → "2.3-draft.10"
        """
        if "-draft." in version:
            base, _, n = version.rpartition("-draft.")
            try:
                return f"{base}-draft.{int(n) + 1}"
            except ValueError:
                pass
        return f"{version}-draft.1"

    _IMMUTABLE_STATES = frozenset({ContractStatus.ACTIVE, ContractStatus.REVIEW})

    def _assert_rules_mutable(self, contract: "DataContract") -> None:
        """Registry-level gate: rules may only change while a contract is DRAFT or ARCHIVED.

        CRT178 #6: the REST layer blocked ACTIVE only, and the registry blocked
        nothing — a contract under REVIEW could be edited after the reviewer
        looked at it (maker-checker TOCTOU), and any non-REST caller could edit
        anything. Enforced here so every caller inherits it.
        """
        if contract.status in self._IMMUTABLE_STATES:
            state = contract.status.value
            hint = (
                "reject it back to DRAFT (or approve it) before editing"
                if contract.status == ContractStatus.REVIEW
                else "create a new draft version to modify rules"
            )
            raise ContractImmutableError(
                f"Contract '{contract.name}' is {state.upper()}: rule mutations are not permitted — {hint}."
            )

    def add_rule(self, name: str, rule_dict: dict) -> "DataContract":
        """Add a new rule to a contract. Validates, writes YAML atomically, triggers reload, records history."""
        from .rule_parser import Rule
        contract = self.get(name)
        if not contract:
            raise ValueError(f"Contract '{name}' not found")
        self._assert_rules_mutable(contract)
        # Validate rule
        rule = Rule(**rule_dict)
        if any(r.name == rule.name for r in contract.rules):
            raise ValueError(f"Rule '{rule.name}' already exists in contract '{name}'")
        contract.rules.append(rule)
        prev_version = contract.version
        # ACT-047-02: Auto-increment draft patch counter while contract is in DRAFT.
        if contract.status == ContractStatus.DRAFT:
            contract.version = self._bump_draft_patch_counter(contract.version)
        self._write_contract_yaml(name, contract, previous_version=prev_version)
        self.history.record_version(contract)
        logger.info("add_rule contract=%s rule=%s version=%s", name, rule.name, contract.version)
        return contract

    def update_rule(self, name: str, rule_name: str, rule_dict: dict) -> tuple["DataContract", bool]:
        """Replace an existing rule. Returns (contract, breaking_change)."""
        from .rule_parser import Rule
        contract = self.get(name)
        if not contract:
            raise ValueError(f"Contract '{name}' not found")
        self._assert_rules_mutable(contract)
        idx = next((i for i, r in enumerate(contract.rules) if r.name == rule_name), None)
        if idx is None:
            raise ValueError(f"Rule '{rule_name}' not found in contract '{name}'")
        old_rule = contract.rules[idx]
        new_rule = Rule(**rule_dict)
        # Detect breaking change
        _breaking_fields = ("type", "pattern", "min_value", "max_value")
        breaking = any(
            getattr(old_rule, f) != getattr(new_rule, f)
            for f in _breaking_fields
            if getattr(old_rule, f) is not None or getattr(new_rule, f) is not None
        )
        contract.rules[idx] = new_rule
        prev_version = contract.version
        # ACT-047-02: Auto-increment draft patch counter while contract is in DRAFT.
        if contract.status == ContractStatus.DRAFT:
            contract.version = self._bump_draft_patch_counter(contract.version)
        self._write_contract_yaml(name, contract, previous_version=prev_version)
        self.history.record_version(contract)
        logger.info("update_rule contract=%s rule=%s breaking=%s version=%s", name, rule_name, breaking, contract.version)
        return contract, breaking

    def delete_rule(self, name: str, rule_name: str) -> "DataContract":
        """Delete a rule from a contract."""
        contract = self.get(name)
        if not contract:
            raise ValueError(f"Contract '{name}' not found")
        self._assert_rules_mutable(contract)
        before = len(contract.rules)
        contract.rules = [r for r in contract.rules if r.name != rule_name]
        if len(contract.rules) == before:
            raise ValueError(f"Rule '{rule_name}' not found in contract '{name}'")
        prev_version = contract.version
        # ACT-047-02: Auto-increment draft patch counter while contract is in DRAFT.
        if contract.status == ContractStatus.DRAFT:
            contract.version = self._bump_draft_patch_counter(contract.version)
        self._write_contract_yaml(name, contract, previous_version=prev_version)
        self.history.record_version(contract)
        logger.info("delete_rule contract=%s rule=%s version=%s", name, rule_name, contract.version)
        return contract

    # Lifecycle + provenance fields that must survive a reload.
    # MUST stay in sync with _parse_contract_format — a field written here but
    # not read there is silently lost on the next reload. (CRT177 Tier 2: the
    # serializer previously wrote only rules+version, so an approval lived in
    # memory only and reverted to the on-disk status on restart/reload.)
    _PERSISTED_META_FIELDS: tuple = (
        "source",
        "proposed_by", "proposed_at",
        "approved_by", "approved_at",
        "rejected_by", "rejected_at", "rejection_reason",
    )

    @classmethod
    def _apply_contract_meta(cls, block: dict, contract: "DataContract") -> None:
        """Write lifecycle status + provenance into a raw ``contract:`` block."""
        block["status"] = contract.status.value
        for field in cls._PERSISTED_META_FIELDS:
            value = getattr(contract, field, None)
            if value is not None:
                block[field] = value

    def _persist_contract_meta(self, name: str, contract: "DataContract") -> None:
        """Persist status + provenance only (no rule changes).

        Used by the lifecycle transitions (set_status, submit, approve, reject).

        A contract with no YAML file on disk (constructed in memory) has nothing
        to persist to — that is a legitimate state, logged at debug and ignored.

        Anything else is reported. A transition that did not reach disk is a lie
        to the caller: memory and history would say ACTIVE while the next reload
        reverts to the on-disk status. That silent divergence is the exact
        defect this method exists to fix, so it must not be swallowed here.
        """
        try:
            path = self._path_for(name, contract.version)
        except KeyError:
            path = None
        if not path or not path.exists():
            logger.debug("No YAML file for contract %s — nothing to persist", name)
            return
        try:
            self._write_contract_yaml(name, contract, include_rules=False)
        except Exception as exc:
            logger.error("Could not persist status/provenance for %s: %s", name, exc)
            raise ContractPersistenceError(
                f"Contract '{name}' transitioned to '{contract.status.value}' in memory but the "
                f"change could not be written to disk; it will revert on the next reload."
            ) from exc

    def _path_for(self, name: str, version: str) -> Path:
        """Resolve the YAML file that holds (name, version).

        Exact (name, version) match wins. If the name maps to exactly one
        distinct file, that file is the answer regardless of the version key —
        the draft patch counter rewrites ``contract.version`` in place on a
        single-file contract, and the re-keying in ``_write_contract_yaml``
        keeps the index current after each write. If the name maps to more than
        one file and the version is not indexed, refuse: guessing is exactly
        the cross-version write-back this index exists to prevent.

        Raises KeyError when nothing is indexed for the name (in-memory only),
        RuntimeError when the target is ambiguous.
        """
        versions = self._contract_paths.get(name) or {}
        if version in versions:
            return versions[version]
        distinct = {p.resolve() for p in versions.values()}
        if not distinct:
            raise KeyError(name)
        if len(distinct) == 1:
            return next(iter(versions.values()))
        raise RuntimeError(
            f"Contract '{name}' v{version} is not mapped to a file and the name is stored across "
            f"{len(distinct)} files — refusing to guess which one to write."
        )

    def _rekey_path(self, name: str, version: str, path: Path) -> None:
        """After a write, make (name, version) → path the only key for that file."""
        versions = self._contract_paths.setdefault(name, {})
        for v in [v for v, p in versions.items() if p.resolve() == path.resolve()]:
            del versions[v]
        versions[version] = path

    def _write_contract_yaml(
        self,
        name: str,
        contract: "DataContract",
        include_rules: bool = True,
        previous_version: Optional[str] = None,
    ) -> None:
        """Write contract state back to the YAML file atomically.

        Always persists lifecycle status + provenance; rules and version are
        written only when ``include_rules`` (the lifecycle transitions do not
        touch rules). ``previous_version`` is the version the file is indexed
        under when the caller has just advanced ``contract.version`` (draft
        patch counter); the index is re-keyed to the new version after the write.
        """
        try:
            path = self._path_for(name, previous_version or contract.version)
        except KeyError:
            path = None
        if not path or not path.exists():
            raise RuntimeError(f"No YAML path found for contract '{name}'")
        # Read existing YAML — safe_load only, no fallback to full_load.
        # full_load can deserialize arbitrary Python objects (security risk).
        # All contracts use plain YAML; Python-specific tags are not supported.
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.constructor.ConstructorError as exc:
            raise RuntimeError(
                f"Contract '{path}' contains unsupported YAML tags "
                f"(e.g. !!python/object). Remove them manually and re-save. "
                f"Detail: {exc}"
            ) from exc
        if "contract" not in raw:
            # Legacy (flat `rules:` list) and onboarding (`fields:`) formats have
            # no `contract:` block. Writing the meta into a block that isn't
            # there would be a silent no-op that loses the transition, so write
            # it at the top level — which is where _parse_legacy_format and
            # _parse_onboarding_format now read it back from. (CRT177 Tier 2)
            self._apply_contract_meta(raw, contract)
        if "contract" in raw:
            if include_rules:
                # Use mode='json' to ensure enum values are serialised as plain strings,
                # not as Python-specific YAML tags (e.g. Severity.ERROR → 'error').
                rules_out = [
                    r.model_dump(by_alias=True, exclude_none=True, mode='json')
                    for r in contract.rules
                ]
                raw["contract"]["rules"] = rules_out
                # ACT-047-02: persist the version field (may have been updated by draft patch counter).
                raw["contract"]["version"] = contract.version
            self._apply_contract_meta(raw["contract"], contract)
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
        self._rekey_path(name, contract.version, path)

    def get_rules_with_context(self, contract: DataContract, context: Optional[str] = None) -> list[Rule]:
        """
        Get rules from a contract, applying context overrides if specified.

        Context overrides can modify existing rules or add new field constraints.

        When the caller wants to know whether the context was actually declared on
        the contract (e.g. to surface a transparent warning to API consumers
        without changing the fail-open behaviour), use
        ``get_rules_with_context_status`` instead — this method preserves the
        legacy single-return-value shape for existing callers.
        """
        if not context:
            return contract.rules
        if context not in contract.contexts:
            logger.debug(
                "Context '%s' not defined in contract '%s' — applying base rules (no overrides). "
                "This is normal for stats-tagging contexts (e.g. 'demo', 'ci', 'test').",
                context, contract.name,
            )
            return contract.rules

        overrides = contract.contexts[context]
        if not isinstance(overrides, dict):
            # 2.8.0: a malformed block used to raise AttributeError per request
            # (HTTP 500); as a ValueError it is refused at load with the context named.
            raise ValueError(f"context overrides must be a mapping of rule-or-field name to override, got {type(overrides).__name__}")
        rules = list(contract.rules)

        # Override resolution order (CRT173):
        #   1. Rule-name match — most specific. Modifies a single named rule
        #      so its error envelope (field, error_code, suggested_fix) stays
        #      bound to the original rule's field and type. This is the path
        #      contracts like proof_of_play.yaml use, where `revenue_ceiling`
        #      and `dwell_seconds_max` are rule names, not column names.
        #   2. Field-name match — broad. Modifies every rule on that field,
        #      e.g. customer.yaml's `kids_app` retargets both age_minimum
        #      and age_reasonable in one stroke.
        #   3. Fallback — mint a synthetic constraint. Reserved for genuinely
        #      new constraints; previously this branch silently swallowed
        #      mis-keyed overrides and produced phantom rules whose `field`
        #      was the rule name (poisoning top_failing_fields[]).
        for key, override in overrides.items():
            rule_match_idx = next((i for i, r in enumerate(rules) if r.name == key), None)
            if rule_match_idx is not None:
                rule_dict = rules[rule_match_idx].model_dump(by_alias=True)
                rule_dict.update(override)
                rules[rule_match_idx] = Rule(**rule_dict)
                continue

            field_match_indices = [i for i, r in enumerate(rules) if r.field == key]
            if field_match_indices:
                for i in field_match_indices:
                    rule_dict = rules[i].model_dump(by_alias=True)
                    rule_dict.update(override)
                    rules[i] = Rule(**rule_dict)
                continue

            override_rule = {
                "name": f"ctx_{context}_{key}",
                "field": key,
                "type": override.get("type", "not_empty"),
                "error_message": override.get("error_message", f"Context {context}: invalid {key}"),
                **{k: v for k, v in override.items() if k not in ("type", "error_message")},
            }
            rules.append(Rule(**override_rule))

        return rules

    def get_rules_with_context_status(
        self, contract: DataContract, context: Optional[str] = None,
    ) -> tuple[list[Rule], str]:
        """Same as get_rules_with_context, plus a status string.

        Status values:
          - ``"none"``      — no context was provided
          - ``"declared"``  — context was provided AND declared on the contract
          - ``"undeclared"`` — context was provided but NOT declared on the contract
                              (engine still returns base rules per fail-open
                              design — see ``get_rules_with_context`` — but the
                              caller can surface a transparent warning to API
                              consumers).

        Closes v2.3.17 F-D: contexts double as both override lookups and
        stats-tagging metadata, so undeclared contexts are not errors. But the
        engine should not silently accept a typo (`prodd` for `prod`) without
        making the divergence visible. This method gives REST and MCP routes
        the signal they need to populate a ``context_warning`` field on the
        validate response without changing fail-open behaviour.
        """
        if not context:
            return contract.rules, "none"
        if context not in (contract.contexts or {}):
            return self.get_rules_with_context(contract, context), "undeclared"
        return self.get_rules_with_context(contract, context), "declared"
