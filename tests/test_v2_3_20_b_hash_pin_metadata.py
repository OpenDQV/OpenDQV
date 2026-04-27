"""
v2.3.20 Cluster B (P1.3) — hash-pin metadata audit-replay fix.

Persona B 2026-04-27 outside-review P1.3:
> Pinning to an older entry_hash correctly applies the historical rules
> (verified via behaviour change) but the response's
> contract_hash/entry_hash/content_hash echo the latest contract identity.
> Only effective_rule_hash reflects what actually ran. Customer impact:
> regulatory replay artefacts look like they were validated against the
> current contract; a regulator asking "which rules ran?" gets a
> misleading answer unless the consumer knows to read effective_rule_hash.

Fix: ``_contract_from_snapshot`` now attaches the snapshot's own
``_snap_entry_hash`` and ``_snap_content_hash`` to the rebuilt contract
object. The validate route prefers those when present (i.e. the contract
came from ``contract_by_hash``) and only falls back to ``_get_contract_hash``
(live head) for non-pinned validates.

Recurrence: pinned validate response's entry_hash + content_hash equal
the pinned snapshot's, NOT the live head's.
"""



class TestHashPinMetadata:
    def test_pinned_validate_echoes_pinned_hashes(self, client, auth_headers):
        """Force a 2-row history state on a bundled contract by mutating
        its description and re-recording, then validate against the older
        hash and assert the response echoes the pinned snapshot."""
        import opendqv.api.deps as _d

        # Pick a stable bundled contract
        target = "customer"
        contract = _d.registry.get(target)
        assert contract is not None

        # Capture current (pre-mutation) head hash as the "old" we'll pin to
        history_before = _d.registry.get_history(target)
        assert history_before, "expected history rows for bundled customer contract"
        old_entry = history_before[-1]["entry_hash"]
        old_content = history_before[-1]["content_hash"]

        # Mutate the contract object's description and record_version to
        # force a new history row with a distinct hash.
        original_description = contract.description
        contract.description = original_description + " [P1.3 test mutation]"
        try:
            _d.registry.history.record_version(contract)
            history_after = _d.registry.get_history(target)
            assert len(history_after) >= len(history_before) + 1, \
                "history should have grown by at least one row"
            new_entry = history_after[-1]["entry_hash"]
            assert new_entry != old_entry, "mutation should produce a distinct hash"

            # Pinned validate against the older hash.
            body = {
                "contract": target,
                "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
                "hash": old_entry,
            }
            r = client.post(
                "/api/v1/validate?allow_draft=true", json=body, headers=auth_headers,
            )
            assert r.status_code == 200, r.text
            resp = r.json()
            assert resp["entry_hash"] == old_entry, (
                f"v2.3.20 P1.3 regression: pinned validate echoed "
                f"{resp['entry_hash']!r} instead of pinned {old_entry!r}."
            )
            assert resp["content_hash"] == old_content
            assert resp["contract_hash"] == old_entry  # Deprecated alias

            # Unpinned validate echoes the live head (NEW hash).
            r2 = client.post(
                "/api/v1/validate?allow_draft=true",
                json={"contract": target, "record": body["record"]},
                headers=auth_headers,
            )
            assert r2.status_code == 200
            live = r2.json()
            assert live["entry_hash"] == new_entry
            assert live["entry_hash"] != old_entry
        finally:
            # Restore the contract description so other tests aren't affected
            contract.description = original_description
