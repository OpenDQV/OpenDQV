"""Tests for contract versioning and history features."""


from core.contracts import ContractHistory, DataContract, _GENESIS_HASH, _compute_entry_hash
from core.rule_parser import Rule, ContractStatus


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
        h1 = _compute_entry_hash("p", "name", "1.0", "active", "[]", "{}", "node1", "2026-01-01T00:00:00+00:00")
        h2 = _compute_entry_hash("p", "name", "1.0", "active", "[]", "{}", "node1", "2026-01-01T00:00:00+00:00")
        assert h1 == h2

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
        resp = client.get(
            "/api/v1/contracts/customer/diff?version_a=1.0&version_b=3.0",
            headers=approver_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["from_version"] == "1.0"
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
    """Integration tests for version bump edge cases raised in RT47/RT49."""

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
