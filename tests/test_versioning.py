"""Tests for contract versioning and history features."""


from opendqv.core.contracts import (
    ContractHistory,
    ContractRegistry,
    DataContract,
    _GENESIS_HASH,
    _compute_entry_hash,
    _version_sort_key,
)
from opendqv.core.rule_parser import Rule, ContractStatus


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestContractHistory:
    """Unit tests for ContractHistory."""

    def _make_contract(self, name="test", version="1.0", status=ContractStatus.ACTIVE, rules=None):
        return DataContract(
            name=name,
            version=version,
            status=status,
            rules=rules or [],
        )

    def _make_rule(self, name, field="col", rule_type="not_empty", **kwargs):
        return Rule(name=name, field=field, type=rule_type, **kwargs)

    # 1
    def test_record_and_get_history(self):
        history = ContractHistory(db_path=":memory:")
        contract = self._make_contract(version="1.0")
        history.record_version(contract)

        result = history.get_history("test")
        assert len(result) == 1
        assert result[0]["version"] == "1.0"

    # 2
    def test_duplicate_snapshots_skipped(self):
        history = ContractHistory(db_path=":memory:")
        contract = self._make_contract(version="1.0")
        history.record_version(contract)
        history.record_version(contract)

        result = history.get_history("test")
        assert len(result) == 1

    # 3
    def test_diff_rules_added(self):
        history = ContractHistory(db_path=":memory:")

        v1 = self._make_contract(version="1.0", rules=[
            self._make_rule("r1", field="a"),
            self._make_rule("r2", field="b"),
        ])
        history.record_version(v1)

        v2 = self._make_contract(version="2.0", rules=[
            self._make_rule("r1", field="a"),
            self._make_rule("r2", field="b"),
            self._make_rule("r3", field="c"),
        ])
        history.record_version(v2)

        diff = history.diff("test", "1.0", "2.0")
        assert len(diff["changes"]["rules_added"]) == 1
        assert diff["changes"]["rules_added"][0]["name"] == "r3"
        assert len(diff["changes"]["rules_removed"]) == 0

    # 4
    def test_diff_rules_removed(self):
        history = ContractHistory(db_path=":memory:")

        v1 = self._make_contract(version="1.0", rules=[
            self._make_rule("r1", field="a"),
            self._make_rule("r2", field="b"),
            self._make_rule("r3", field="c"),
        ])
        history.record_version(v1)

        v2 = self._make_contract(version="2.0", rules=[
            self._make_rule("r1", field="a"),
            self._make_rule("r2", field="b"),
        ])
        history.record_version(v2)

        diff = history.diff("test", "1.0", "2.0")
        assert len(diff["changes"]["rules_removed"]) == 1
        assert diff["changes"]["rules_removed"][0]["name"] == "r3"
        assert len(diff["changes"]["rules_added"]) == 0

    # 5
    def test_diff_rules_changed(self):
        history = ContractHistory(db_path=":memory:")

        v1 = self._make_contract(version="1.0", rules=[
            self._make_rule("email_check", field="email", rule_type="regex", pattern=r"^.+@.+$"),
        ])
        history.record_version(v1)

        v2 = self._make_contract(version="2.0", rules=[
            self._make_rule("email_check", field="email", rule_type="regex",
                            pattern=r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"),
        ])
        history.record_version(v2)

        diff = history.diff("test", "1.0", "2.0")
        assert len(diff["changes"]["rules_changed"]) == 1
        changed = diff["changes"]["rules_changed"][0]
        assert changed["name"] == "email_check"
        assert "pattern" in changed["changes"]

    # 6
    def test_diff_metadata_changed(self):
        history = ContractHistory(db_path=":memory:")

        v1 = self._make_contract(version="1.0", status=ContractStatus.DRAFT)
        history.record_version(v1)

        v2 = self._make_contract(version="2.0", status=ContractStatus.ACTIVE)
        history.record_version(v2)

        diff = history.diff("test", "1.0", "2.0")
        mc = diff["changes"]["metadata_changed"]
        assert "status" in mc
        assert mc["status"]["old"] == "draft"
        assert mc["status"]["new"] == "active"


class TestHashChainAuditLog:
    """SHA-256 forward-linked hash chain on contract_history rows."""

    def _make_contract(self, name="chain_test", version="1.0", rules=None):
        return DataContract(name=name, version=version, rules=rules or [])

    def _make_rule(self, name, field="col"):
        return Rule(name=name, field=field, type="not_empty")

    # 1
    def test_genesis_prev_hash(self):
        h = ContractHistory(db_path=":memory:")
        h.record_version(self._make_contract())
        row = h.get_history("chain_test")[0]
        assert row["prev_hash"] == _GENESIS_HASH

    # 2
    def test_entry_hash_non_empty(self):
        h = ContractHistory(db_path=":memory:")
        h.record_version(self._make_contract())
        row = h.get_history("chain_test")[0]
        assert len(row["entry_hash"]) == 64  # hex SHA-256
        assert row["entry_hash"] != _GENESIS_HASH

    # 3
    def test_chain_forward_linked(self):
        h = ContractHistory(db_path=":memory:")
        h.record_version(self._make_contract(version="1.0"))
        h.record_version(self._make_contract(version="2.0"))
        rows = h.get_history("chain_test")
        assert len(rows) == 2
        assert rows[1]["prev_hash"] == rows[0]["entry_hash"]

    # 4
    def test_chain_integrity_three_entries(self):
        h = ContractHistory(db_path=":memory:")
        h.record_version(self._make_contract(version="1.0"))
        h.record_version(self._make_contract(version="2.0"))
        h.record_version(self._make_contract(version="3.0"))
        rows = h.get_history("chain_test")
        assert rows[0]["prev_hash"] == _GENESIS_HASH
        assert rows[1]["prev_hash"] == rows[0]["entry_hash"]
        assert rows[2]["prev_hash"] == rows[1]["entry_hash"]

    # 5
    def test_dedup_does_not_extend_chain(self):
        h = ContractHistory(db_path=":memory:")
        c = self._make_contract()
        h.record_version(c)
        h.record_version(c)  # exact duplicate — should be skipped
        assert len(h.get_history("chain_test")) == 1

    # 6
    def test_compute_entry_hash_deterministic(self):
        kwargs = dict(
            prev_hash="p", contract_name="name", version="1.0", status="active",
            owner="", owner_email=None, owner_team=None, asset_id=None,
            description="", downstream_consumers=[], rules=[], contexts={},
            opendqv_node_id="node1", updated_at="2026-01-01T00:00:00+00:00",
        )
        assert _compute_entry_hash(**kwargs) == _compute_entry_hash(**kwargs)

    # 7
    def test_different_contracts_have_independent_chains(self):
        h = ContractHistory(db_path=":memory:")
        h.record_version(DataContract(name="alpha", version="1.0"))
        h.record_version(DataContract(name="beta", version="1.0"))
        h.record_version(DataContract(name="alpha", version="2.0"))
        alpha = h.get_history("alpha")
        beta = h.get_history("beta")
        assert alpha[0]["prev_hash"] == _GENESIS_HASH
        assert beta[0]["prev_hash"] == _GENESIS_HASH
        # alpha chain is independent of beta
        assert alpha[1]["prev_hash"] == alpha[0]["entry_hash"]


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestVersioningAPI:
    """Integration tests for versioning API endpoints."""

    # 7
    def test_get_history(self, client, auth_headers):
        resp = client.get("/api/v1/contracts/customer/history", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "history" in body
        assert isinstance(body["history"], list)

    # 8
    def test_bump_version(self, client, approver_headers):
        resp = client.post(
            "/api/v1/contracts/customer/version?new_version=2.0",
            headers=approver_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "diff" in body
        assert body["new_version"] == "2.0"
        assert body["status"] == "draft"

    # 9
    def test_diff_endpoint(self, client, approver_headers):
        # Ensure v3.0 exists by bumping first (requires approver role)
        client.post(
            "/api/v1/contracts/customer/version?new_version=3.0",
            headers=approver_headers,
        )
        import os
        import pathlib
        import yaml as _yaml
        base_version = str(_yaml.safe_load(
            (pathlib.Path(os.environ["OPENDQV_CONTRACTS_DIR"]) / "customer.yaml").read_text(encoding="utf-8")
        )["contract"]["version"])  # the bundled version bumps with the golden library (2.7.0)
        resp = client.get(
            f"/api/v1/contracts/customer/diff?version_a={base_version}&version_b=3.0",
            headers=approver_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["from_version"] == base_version
        assert body["to_version"] == "3.0"

    # 10
    def test_history_not_found(self, client, auth_headers):
        resp = client.get("/api/v1/contracts/nonexistent/history", headers=auth_headers)
        assert resp.status_code == 404

    def test_validator_cannot_bump_version(self, client, auth_headers):
        """Maker-checker: version creation requires approver or admin role."""
        resp = client.post(
            "/api/v1/contracts/customer/version?new_version=99.0",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    # 11
    def test_versioning_requires_auth(self, client):
        endpoints = [
            ("GET", "/api/v1/contracts/customer/history"),
            ("POST", "/api/v1/contracts/customer/version?new_version=9.0"),
            ("GET", "/api/v1/contracts/customer/diff?version_a=1.0&version_b=2.0"),
        ]
        for method, url in endpoints:
            if method == "GET":
                resp = client.get(url)
            else:
                resp = client.post(url)
            assert resp.status_code == 401, f"{method} {url} should require auth, got {resp.status_code}"

# ---------------------------------------------------------------------------
# ACT-049-12: Versioning edge cases — integration-level coverage for
# the contract fork workflow and version bump behaviour.
# ---------------------------------------------------------------------------

class TestVersioningEdgeCases:
    """Integration tests for version bump edge cases identified during version bump testing."""

    def test_fork_creates_draft_status(self, client, approver_headers):
        """Bumping an ACTIVE contract must always produce a DRAFT — never ACTIVE."""
        resp = client.post(
            "/api/v1/contracts/customer/version?new_version=10.1",
            headers=approver_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "draft"

    def test_fork_new_version_matches_requested(self, client, approver_headers):
        """The new_version field in the response must match the requested version."""
        resp = client.post(
            "/api/v1/contracts/customer/version?new_version=10.2",
            headers=approver_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["new_version"] == "10.2"

    def test_fork_diff_shows_no_rule_changes(self, client, approver_headers):
        """A freshly forked version has the same rules — diff should show no rule changes."""
        resp = client.post(
            "/api/v1/contracts/customer/version?new_version=10.3",
            headers=approver_headers,
        )
        assert resp.status_code == 200
        diff = resp.json()["diff"]
        assert diff["changes"]["rules_added"] == []
        assert diff["changes"]["rules_removed"] == []
        assert diff["changes"]["rules_changed"] == []

    def test_fork_records_created_by(self, client, approver_headers):
        """Version bump with created_by param must appear in the new contract's history."""
        resp = client.post(
            "/api/v1/contracts/customer/version?new_version=10.4&created_by=pytest-edge",
            headers=approver_headers,
        )
        assert resp.status_code == 200
        history_resp = client.get("/api/v1/contracts/customer/history", headers=approver_headers)
        assert history_resp.status_code == 200
        entries = history_resp.json()["history"]
        versions = [e["version"] for e in entries]
        assert "10.4" in versions

    def test_duplicate_version_bump_is_idempotent_or_rejected(self, client, approver_headers):
        """Requesting the same new version twice must either be idempotent or return an error — never silently overwrite."""
        resp1 = client.post(
            "/api/v1/contracts/customer/version?new_version=10.5",
            headers=approver_headers,
        )
        assert resp1.status_code == 200
        resp2 = client.post(
            "/api/v1/contracts/customer/version?new_version=10.5",
            headers=approver_headers,
        )
        # Either 200 (idempotent), 400 (duplicate version rejected), or 409 (conflict) — not 500
        assert resp2.status_code in (200, 400, 409)

    def test_diff_on_missing_version_returns_404(self, client, approver_headers):
        """Diff between a real version and a nonexistent version must return 404, not 500."""
        resp = client.get(
            "/api/v1/contracts/customer/diff?version_a=1.0&version_b=999.999",
            headers=approver_headers,
        )
        assert resp.status_code == 404

    def test_history_grows_after_version_bump(self, client, approver_headers):
        """Each version bump must add an entry to the contract history."""
        before = client.get("/api/v1/contracts/customer/history", headers=approver_headers)
        count_before = len(before.json()["history"])
        client.post(
            "/api/v1/contracts/customer/version?new_version=10.6",
            headers=approver_headers,
        )
        after = client.get("/api/v1/contracts/customer/history", headers=approver_headers)
        count_after = len(after.json()["history"])
        assert count_after > count_before


class TestHistoricalHashEcho:
    """
    CRT169 follow-up (v2.3.2) — when ?hash=<historical_hash> is supplied,
    the response must echo the SAME entry_hash that was requested, not the
    current latest hash. Bug shape: with two history entries sharing a
    version (e.g. two v1.0 snapshots after an in-place description edit
    plus reload), the buggy code matched by version alone and reported
    the latest entry's hashes for any historical lookup.
    """

    def test_historical_hash_lookup_echoes_requested_hash(
        self, client, approver_headers
    ):
        import os
        import pathlib
        from opendqv.api import deps as _api_deps

        contracts_dir = pathlib.Path(os.environ["OPENDQV_CONTRACTS_DIR"])
        yaml_path = contracts_dir / "customer.yaml"
        original_yaml = yaml_path.read_text(encoding="utf-8")

        import yaml as _yaml
        on_disk_version = _yaml.safe_load(original_yaml)["contract"]["version"]

        try:
            hist_before = client.get(
                "/api/v1/contracts/customer/history", headers=approver_headers
            ).json()["history"]
            v_entries_before = [h for h in hist_before if h["version"] == on_disk_version]
            assert v_entries_before, (
                f"fixture must have at least one v{on_disk_version} history entry"
            )
            original_entry_hash = v_entries_before[-1]["entry_hash"]

            # Mutate a plain-scalar field that every bundled contract carries
            # (the golden library's description is a block scalar, 2.7.0).
            mutated = original_yaml.replace(
                "owner_email: opendqv@bgmsconsultants.com",
                "owner_email: opendqv-v2@bgmsconsultants.com",
                1,
            )
            assert mutated != original_yaml, "owner_email marker not found in fixture"
            yaml_path.write_text(mutated, encoding="utf-8")

            _api_deps.registry.reload()

            hist_after = client.get(
                "/api/v1/contracts/customer/history", headers=approver_headers
            ).json()["history"]
            v_entries_after = [h for h in hist_after if h["version"] == on_disk_version]
            assert len(v_entries_after) == len(v_entries_before) + 1, (
                "in-place description edit must record exactly one new "
                f"v{on_disk_version} history entry"
            )
            new_entry_hash = v_entries_after[-1]["entry_hash"]
            assert new_entry_hash != original_entry_hash, (
                "content change must produce a different entry_hash"
            )

            old_resp = client.get(
                f"/api/v1/contracts/customer?hash={original_entry_hash}",
                headers=approver_headers,
            )
            assert old_resp.status_code == 200, old_resp.text
            old_body = old_resp.json()
            assert old_body["entry_hash"] == original_entry_hash
            assert old_body["contract_hash"] == original_entry_hash
            assert old_body["content_hash"] is not None
            old_content_hash = old_body["content_hash"]

            new_resp = client.get(
                f"/api/v1/contracts/customer?hash={new_entry_hash}",
                headers=approver_headers,
            )
            assert new_resp.status_code == 200, new_resp.text
            new_body = new_resp.json()
            assert new_body["entry_hash"] == new_entry_hash
            assert new_body["content_hash"] is not None
            assert new_body["content_hash"] != old_content_hash, (
                "different content must produce different content_hash"
            )

            content_hash_resp = client.get(
                f"/api/v1/contracts/customer?hash={old_content_hash}",
                headers=approver_headers,
            )
            assert content_hash_resp.status_code == 200, content_hash_resp.text
            content_hash_body = content_hash_resp.json()
            assert content_hash_body["content_hash"] == old_content_hash, (
                "content_hash round-trip: response must echo the requested content_hash"
            )
            assert content_hash_body["entry_hash"] is not None
        finally:
            yaml_path.write_text(original_yaml, encoding="utf-8")
            _api_deps.registry.reload()


class TestHashDomainCompleteness:
    """
    CRT169 guard — every DataContract field is either part of the v2 hash
    domain or explicitly excluded. Adding a new field to DataContract without
    updating one of these sets causes this test to fail, forcing the author
    to make a deliberate decision instead of silently dropping it from the
    chain.
    """

    EXCLUDED_FROM_HASH = frozenset({
        "catalog_visible",
        "sensitive_fields",
        "validate_in_states",
        "last_active_snapshot",
        "source",
        "proposed_by", "proposed_at",
        "reviewed_by", "reviewed_at",
        "approved_by", "approved_at",
        "rejected_by", "rejected_at", "rejection_reason",
        # CRT180 review B3: snapshot-side strict settings — lifecycle metadata like last_active_snapshot
        "last_active_strict_schema", "last_active_fields",
    })

    def test_every_data_contract_field_classified(self):
        from opendqv.core.contracts import (
            DataContract, _HASH_DOMAIN_CONTENT_FIELDS,
        )

        all_fields = set(DataContract.model_fields.keys())
        content = set(_HASH_DOMAIN_CONTENT_FIELDS)
        excluded = self.EXCLUDED_FROM_HASH

        overlap = content & excluded
        assert not overlap, (
            f"Fields appear in both hash domain and exclusion list: {overlap}. "
            "A field cannot be both."
        )

        unclassified = all_fields - content - excluded
        assert not unclassified, (
            f"DataContract fields not classified: {unclassified}. "
            "Add to _HASH_DOMAIN_CONTENT_FIELDS in opendqv/core/contracts.py "
            "(if semantically meaningful for audit replay) or to EXCLUDED_FROM_HASH "
            "in this test (if intentionally excluded — e.g. lifecycle metadata, "
            "display flags, server-set provenance)."
        )

        ghosts = content - all_fields
        assert not ghosts, (
            f"_HASH_DOMAIN_CONTENT_FIELDS references unknown DataContract fields: "
            f"{ghosts}. Field renamed or removed without updating the hash domain."
        )


# ---------------------------------------------------------------------------
# CRT175 #4: "latest" version resolution for draft-suffixed versions
# ---------------------------------------------------------------------------

class TestVersionSortKey:
    """The old sort key collapsed every '-draft.N' version to a constant, so
    'latest' resolution among drafts was order-dependent. _version_sort_key
    must give a total, deterministic ordering."""

    def test_numeric_base_ordering(self):
        assert _version_sort_key("2.0") > _version_sort_key("1.9")
        assert _version_sort_key("1.10") > _version_sort_key("1.9")
        assert _version_sort_key("1.0.1") > _version_sort_key("1.0")

    def test_released_outranks_its_drafts(self):
        assert _version_sort_key("1.0") > _version_sort_key("1.0-draft.5")

    def test_higher_draft_counter_is_later(self):
        assert _version_sort_key("1.0-draft.2") > _version_sort_key("1.0-draft.1")
        assert _version_sort_key("2.3-draft.10") > _version_sort_key("2.3-draft.9")

    def test_higher_base_beats_lower_base_draft(self):
        assert _version_sort_key("2.0") > _version_sort_key("1.0-draft.99")

    def test_latest_among_drafts_is_deterministic_regardless_of_insertion_order(self):
        keys = ["1.0", "1.0-draft.1", "1.0-draft.2"]
        # The old bug: both drafts sorted to the same constant key, so the
        # winner depended on dict/iteration order. Both orderings must agree.
        assert sorted(keys, key=_version_sort_key)[-1] == "1.0"
        assert sorted(reversed(keys), key=_version_sort_key)[-1] == "1.0"

    def test_latest_is_highest_draft_when_no_release_exists(self):
        keys = ["1.0-draft.1", "1.0-draft.3", "1.0-draft.2"]
        assert sorted(keys, key=_version_sort_key)[-1] == "1.0-draft.3"

    def test_malformed_versions_do_not_tie_and_are_deterministic(self):
        """Sonnet red-team (CRT175): a hand-typed 'X.Y-draft' with no counter,
        or other odd strings, used to coerce to the same key as a clean release
        and tie — leaving 'latest' iteration-order dependent. The raw-string
        tiebreak must make the order total and stable regardless of input order."""
        keys = ["1.0", "1.0-draft", "1.0.0", "banana", ""]
        a = sorted(keys, key=_version_sort_key)
        b = sorted(reversed(keys), key=_version_sort_key)
        assert a == b  # deterministic regardless of insertion order
        # distinct strings never produce equal keys → no ties
        skeys = [_version_sort_key(k) for k in keys]
        assert len(set(skeys)) == len(skeys)

    def test_sort_key_never_raises_on_arbitrary_strings(self):
        for v in ["", "1", "1.0", "1.0.0", "1.0-draft", "1.0-draft.x",
                  "v2", "1..0", "-", "1.0-draft.-1"]:
            _version_sort_key(v)  # must not raise


class TestRegistryLatestResolution:
    """End-to-end: registry.get(name, 'latest') must be stable with drafts present."""

    def test_get_latest_picks_highest_draft(self, tmp_path):
        reg = ContractRegistry(tmp_path)
        base = DataContract(
            name="widget", version="1.0", description="d", owner="o",
            status=ContractStatus.DRAFT,
            rules=[Rule(name="r", type="not_empty", field="id")],
        )
        reg._contracts["widget"] = {
            "1.0-draft.1": base.model_copy(update={"version": "1.0-draft.1"}),
            "1.0-draft.3": base.model_copy(update={"version": "1.0-draft.3"}),
            "1.0-draft.2": base.model_copy(update={"version": "1.0-draft.2"}),
        }
        assert reg.get("widget", "latest").version == "1.0-draft.3"
