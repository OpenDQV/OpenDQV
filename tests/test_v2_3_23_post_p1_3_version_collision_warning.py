"""
v2.3.23 P1-3 — list_versions surface honesty about version-string collision.

Persona B inside-view 2026-04-28:
  "list_versions returned five entries all labelled version: '1.0';
   compare_contracts between two of them showed
   trade_date_matches_execution_date had its type changed from regex
   to compare. That is a behavioural change shipped under the same
   version string. Customer impact: an audit citation of 'MiFID
   contract v1.0' cannot be tied to a specific rule set without
   falling back to the entry_hash."

Sonnet's pre-impl review (a5f385c20eba96e85) directed:
  - Per-entry `is_collision: bool` flag on each ContractVersionSummary.
  - No top-level `version_label_collision_detected`.
  - No `is_canonical` (would bake in a v2.4 selection rule we haven't
    decided yet).
  - Outcome-coupled test: load two history entries with same version
    string and different content_hashes; assert both have
    is_collision: True.

The architectural fix (write-time uniqueness enforcement on
(name, version)) stays v2.4 / F-C / project_v240_known_ceiling.md.
v2.3.23 just makes the collision visible on the read surface.
"""



class TestVersionLabelCollisionWarning:
    """is_collision flags entries that share a version string with at
    least one other entry of different content_hash. The flag is per-
    entry so a consumer iterating versions can branch without re-
    parsing the whole list."""

    def test_collision_flagged_when_two_entries_share_version_string(
        self, client, monkeypatch
    ):
        """Outcome-coupled test (Sonnet's directive): patch
        registry.get_history to return two synthetic entries with
        same version + different content_hashes; assert BOTH carry
        is_collision: True."""
        import opendqv.api.deps as _d

        synthetic_history = [
            {
                "version": "1.0", "status": "archived",
                "entry_hash": "entry_a", "content_hash": "content_a",
                "updated_at": "2026-04-28T00:00:00+00:00",
                "owner": "test", "owner_team": None,
                "approved_by": "alice", "proposed_by": "alice",
                "rules": [],
            },
            {
                "version": "1.0", "status": "active",
                "entry_hash": "entry_b", "content_hash": "content_b",
                "updated_at": "2026-04-28T01:00:00+00:00",
                "owner": "test", "owner_team": None,
                "approved_by": "alice", "proposed_by": "alice",
                "rules": [],
            },
        ]
        original_get_history = _d.registry.get_history
        original_get = _d.registry.get

        def patched_get_history(name):
            if name == "p1_3_probe":
                return synthetic_history
            return original_get_history(name)

        def patched_get(name, version="latest"):
            if name == "p1_3_probe":
                return None  # route's `if not history and not registry.get` falls through to history truthiness
            return original_get(name, version)

        monkeypatch.setattr(_d.registry, "get_history", patched_get_history)
        monkeypatch.setattr(_d.registry, "get", patched_get)

        r = client.get("/api/v1/contracts/p1_3_probe/versions")
        assert r.status_code == 200, r.text
        body = r.json()
        versions = body.get("versions", [])
        assert len(versions) == 2, versions

        for entry in versions:
            assert entry["is_collision"] is True, (
                f"v2.3.23 P1-3: entry with version='1.0' and distinct "
                f"content_hash must carry is_collision: True. "
                f"Got: {entry}."
            )

    def test_no_collision_flag_on_unique_version_entries(
        self, client, approver_headers
    ):
        """Entries with version strings unique in the history must
        NOT have is_collision: True. The flag is binary, not noise."""
        r = client.get("/api/v1/contracts/customer/versions")
        assert r.status_code == 200, r.text
        versions = r.json().get("versions", [])
        from collections import Counter
        version_counts = Counter(v["version"] for v in versions)
        for v in versions:
            unique = version_counts[v["version"]] == 1
            if unique:
                # is_collision must be False for genuinely-unique versions.
                assert v.get("is_collision") is False, (
                    f"Unique version string {v['version']!r} flagged as "
                    f"collision: {v}"
                )

    def test_field_present_on_every_entry(self, client):
        """is_collision must be present on EVERY entry, not just the
        colliding ones — additive but consistent shape."""
        r = client.get("/api/v1/contracts/customer/versions")
        assert r.status_code == 200, r.text
        versions = r.json().get("versions", [])
        for v in versions:
            assert "is_collision" in v, (
                f"is_collision must be present on every list_versions "
                f"entry (consistent shape). Got: {v}"
            )
            assert isinstance(v["is_collision"], bool), (
                f"is_collision must be a bool. Got: {type(v['is_collision']).__name__}"
            )
