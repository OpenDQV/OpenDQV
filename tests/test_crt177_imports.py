"""
CRT177 Tier 2 — import governance recurrence tests (Protocol 32).

`routes_imports.py` stamped provenance onto the TOP LEVEL of the YAML document
(``doc["status"] = "draft"``) while the loader reads it from the NESTED
``contract:`` block (``_parse_contract_format`` → ``c.get("status", "active")``).
Every stamp was therefore a silent no-op on disk:

  * The CSV importer hardcodes ``status: active`` and the ODCS importer takes a
    caller-controlled ``info.status`` (default ``active``) inside the nested
    block — so `?save=true` on those two persisted contracts as **ACTIVE**:
    immediately live for validation, immutable to rule edits, and never routed
    through draft → review → approve. An `editor` achieved ACTIVE with no
    approver. The API response meanwhile reported ``status: draft``, so response
    and persisted state disagreed.
  * ``source`` and ``created_by`` were dropped on ALL EIGHT importers, losing
    import provenance from the audit trail.
"""
import yaml

import pytest

from opendqv.api.routes_imports import _apply_import_meta
from opendqv.core.contracts import ContractRegistry
from opendqv.core.rule_parser import ContractStatus


def _roundtrip(tmp_path, contract_body, created_by=""):
    """Stamp a contract doc the way an import save does, then load it back."""
    doc = yaml.safe_load(yaml.safe_dump({"contract": contract_body}))
    _apply_import_meta(doc, created_by)
    (tmp_path / f"{contract_body['name']}.yaml").write_text(
        yaml.safe_dump(doc), encoding="utf-8"
    )
    return ContractRegistry(tmp_path).get(contract_body["name"])


class TestImportAlwaysLandsAsDraft:
    @pytest.mark.parametrize("declared_status", ["active", "draft", "archived", None])
    def test_status_forced_to_draft_on_disk(self, tmp_path, declared_status):
        """Whatever the importer emitted, a saved import must load as DRAFT."""
        body = {"name": "imp1", "version": "1.0", "rules": []}
        if declared_status is not None:
            body["status"] = declared_status
        c = _roundtrip(tmp_path, body)
        assert c.status == ContractStatus.DRAFT, (
            f"importer-declared status {declared_status!r} must not survive — "
            f"an import must never land ACTIVE and skip approval"
        )

    def test_csv_style_active_contract_is_neutralised(self, tmp_path):
        """csv_rules.py hardcodes status: active — the ACTIVE-bypass source."""
        c = _roundtrip(tmp_path, {"name": "imp_csv", "version": "1.0",
                                  "status": "active", "rules": []})
        assert c.status == ContractStatus.DRAFT

    def test_odcs_style_caller_controlled_status_is_neutralised(self, tmp_path):
        """odcs.py takes status from caller-supplied info.status."""
        c = _roundtrip(tmp_path, {"name": "imp_odcs", "version": "1.0",
                                  "status": "active", "rules": []})
        assert c.status == ContractStatus.DRAFT


class TestImportProvenanceReachesDisk:
    def test_source_is_recorded(self, tmp_path):
        c = _roundtrip(tmp_path, {"name": "imp2", "version": "1.0", "rules": []})
        assert c.source == "import"

    def test_created_by_is_recorded_as_proposed_by(self, tmp_path):
        """created_by maps to proposed_by — the field the loader actually reads."""
        c = _roundtrip(tmp_path, {"name": "imp3", "version": "1.0", "rules": []},
                       created_by="alice")
        assert c.proposed_by == "alice"

    def test_meta_written_into_nested_block_not_top_level(self, tmp_path):
        """The root cause: stamps must land inside `contract:`, not beside it."""
        doc = yaml.safe_load(yaml.safe_dump({"contract": {"name": "imp4", "rules": []}}))
        _apply_import_meta(doc, "bob")
        assert doc["contract"]["status"] == "draft"
        assert doc["contract"]["source"] == "import"
        assert doc["contract"]["proposed_by"] == "bob"
        assert "status" not in doc, "stamp must not be written at the top level"
        assert "source" not in doc


class TestApplyImportMetaIsSafe:
    def test_no_contract_block_is_a_noop(self):
        doc = {"something_else": {}}
        _apply_import_meta(doc, "alice")  # must not raise
        assert "status" not in doc

    def test_non_dict_contract_block_is_a_noop(self):
        doc = {"contract": "not-a-dict"}
        _apply_import_meta(doc, "alice")  # must not raise
        assert doc["contract"] == "not-a-dict"
