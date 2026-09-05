"""
CRT177 Tier 2 — contract state durability recurrence tests (Protocol 32).

The YAML serializer wrote only `rules` and `version`, so lifecycle state and
provenance never reached disk:

  * `submit_for_review` / `approve_contract` / `reject_contract` updated memory
    and history but never wrote back — **an approval reverted to the on-disk
    status on the next restart or `/contracts/reload`** while history claimed
    ACTIVE. `set_status` (the *ungoverned* demote/archive path) did persist, so
    the durability guarantee was exactly inverted.
  * `set_status` persisted via an unanchored ``^( +status: )\\S+`` regex that
    rewrote EVERY indented ``status:`` line in the file — corrupting any nested
    ``status`` key (a rule field named `status`, a context override) — and wrote
    non-atomically.

Both are fixed by serialising status + provenance structurally through the
shared atomic writer.
"""
import os

import yaml

import pytest

from opendqv.core.contracts import (
    ContractPersistenceError,
    ContractRegistry,
    DataContract,
)
from opendqv.core.rule_parser import ContractStatus


def _write(dirpath, name, body):
    (dirpath / f"{name}.yaml").write_text(
        yaml.safe_dump({"contract": body}), encoding="utf-8"
    )


@pytest.fixture
def cdir(tmp_path):
    return tmp_path


class TestLifecycleTransitionsPersist:
    def test_approval_survives_reload(self, cdir):
        _write(cdir, "c1", {"name": "c1", "version": "1.0", "status": "draft", "rules": []})
        reg = ContractRegistry(cdir)
        reg.submit_for_review("c1", "1.0", "alice")
        reg.approve_contract("c1", "1.0", "bob")
        assert reg.get("c1").status == ContractStatus.ACTIVE

        # The regression: a fresh registry reads from disk.
        reloaded = ContractRegistry(cdir).get("c1")
        assert reloaded.status == ContractStatus.ACTIVE, "approval must survive reload"
        assert reloaded.approved_by == "bob"
        assert reloaded.proposed_by == "alice"

    def test_submit_for_review_survives_reload(self, cdir):
        _write(cdir, "c2", {"name": "c2", "version": "1.0", "status": "draft", "rules": []})
        ContractRegistry(cdir).submit_for_review("c2", "1.0", "alice")
        reloaded = ContractRegistry(cdir).get("c2")
        assert reloaded.status == ContractStatus.REVIEW
        assert reloaded.proposed_by == "alice"

    def test_rejection_and_reason_survive_reload(self, cdir):
        _write(cdir, "c3", {"name": "c3", "version": "1.0", "status": "draft", "rules": []})
        reg = ContractRegistry(cdir)
        reg.submit_for_review("c3", "1.0", "alice")
        reg.reject_contract("c3", "1.0", "bob", "insufficient coverage")
        reloaded = ContractRegistry(cdir).get("c3")
        assert reloaded.status == ContractStatus.DRAFT
        assert reloaded.rejected_by == "bob"
        assert reloaded.rejection_reason == "insufficient coverage"

    def test_set_status_still_persists(self, cdir):
        # set_status already persisted; it must keep doing so after the
        # regex → structured-serialisation swap.
        _write(cdir, "c4", {"name": "c4", "version": "1.0", "status": "active", "rules": []})
        ContractRegistry(cdir).set_status("c4", "1.0", ContractStatus.ARCHIVED)
        assert ContractRegistry(cdir).get("c4").status == ContractStatus.ARCHIVED


class TestStatusWriteDoesNotCorruptNestedKeys:
    def test_nested_status_key_not_clobbered(self, cdir):
        """The old regex rewrote every indented `status:` line in the file."""
        _write(cdir, "c5", {
            "name": "c5", "version": "1.0", "status": "active",
            "rules": [{"name": "r1", "field": "status", "type": "not_empty"}],
            # a well-formed override (2.8.0 constructs contexts at load) that keeps a
            # nested `status` key in the file: the rule's field and the override key
            "contexts": {"eu": {"r1": {"error_message": "status is required in the EU"}}},
        })
        ContractRegistry(cdir).set_status("c5", "1.0", ContractStatus.ARCHIVED)

        raw = yaml.safe_load((cdir / "c5.yaml").read_text(encoding="utf-8"))["contract"]
        assert raw["status"] == "archived"                       # lifecycle status changed
        assert raw["rules"][0]["field"] == "status"              # rule field untouched
        assert raw["contexts"]["eu"]["r1"]["error_message"].startswith("status")   # context override untouched

    def test_write_is_atomic_no_tmp_left_behind(self, cdir):
        _write(cdir, "c6", {"name": "c6", "version": "1.0", "status": "draft", "rules": []})
        ContractRegistry(cdir).set_status("c6", "1.0", ContractStatus.ACTIVE)
        assert not list(cdir.glob("*.tmp")), "atomic write must not leave a temp file"


class TestPersistenceFailureIsReported:
    """A transition that did not reach disk must not be reported as success —
    memory/history would say ACTIVE while the next reload reverts it. Found by
    adversarial review of the first cut of this fix, which logged and continued."""

    def test_legacy_format_transition_persists(self, cdir):
        """Flat `rules:` list — no `contract:` block. Status/provenance are
        written at the top level and read back there, so the transition is
        durable rather than a silent no-op."""
        (cdir / "legacy_c.yaml").write_text(
            yaml.safe_dump({"version": "1.0",
                            "rules": [{"name": "r", "field": "f", "type": "not_empty"}]}),
            encoding="utf-8",
        )
        ContractRegistry(cdir).set_status("legacy_c", "1.0", ContractStatus.DRAFT)
        assert ContractRegistry(cdir).get("legacy_c").status == ContractStatus.DRAFT

    def test_legacy_format_approval_provenance_persists(self, cdir):
        (cdir / "legacy_d.yaml").write_text(
            yaml.safe_dump({"version": "1.0", "status": "draft",
                            "rules": [{"name": "r", "field": "f", "type": "not_empty"}]}),
            encoding="utf-8",
        )
        reg = ContractRegistry(cdir)
        reg.submit_for_review("legacy_d", "1.0", "alice")
        reg.approve_contract("legacy_d", "1.0", "bob")
        reloaded = ContractRegistry(cdir).get("legacy_d")
        assert reloaded.status == ContractStatus.ACTIVE
        assert reloaded.approved_by == "bob"

    def test_write_failure_raises(self, cdir):
        _write(cdir, "c8", {"name": "c8", "version": "1.0", "status": "draft", "rules": []})
        reg = ContractRegistry(cdir)
        reg.submit_for_review("c8", "1.0", "alice")
        os.chmod(cdir, 0o500)  # read-only directory → the YAML write fails
        try:
            with pytest.raises(ContractPersistenceError):
                reg.approve_contract("c8", "1.0", "bob")
        finally:
            os.chmod(cdir, 0o700)

    def test_in_memory_contract_without_yaml_is_silent(self, cdir):
        """A contract with no file on disk has nothing to persist to — that is a
        legitimate state and must NOT raise."""
        reg = ContractRegistry(cdir)
        reg._contracts["mem"] = {
            "1.0": DataContract(name="mem", version="1.0",
                                status=ContractStatus.DRAFT, rules=[])
        }
        reg.set_status("mem", "1.0", ContractStatus.ACTIVE)  # must not raise


class TestSerializerReaderSymmetry:
    def test_every_persisted_field_is_read_back(self, cdir):
        """A field written but not read by _parse_contract_format is lost
        silently on reload — this guards that pairing."""
        _write(cdir, "c7", {"name": "c7", "version": "1.0", "status": "draft", "rules": []})
        reg = ContractRegistry(cdir)
        c = reg.get("c7")
        for field in ContractRegistry._PERSISTED_META_FIELDS:
            setattr(c, field, f"val-{field}")
        reg._persist_contract_meta("c7", c)

        reloaded = ContractRegistry(cdir).get("c7")
        for field in ContractRegistry._PERSISTED_META_FIELDS:
            assert getattr(reloaded, field) == f"val-{field}", (
                f"{field!r} is in _PERSISTED_META_FIELDS but _parse_contract_format "
                f"does not read it back — it will be lost on reload"
            )
