"""
v2.3.20 Cluster H (P1.4) — template-level attestation on bundled exemplars.

Persona B 2026-04-27 outside-review P1.4:
> proposed_by and approved_by are both null across all version history
> entries. SoX and DORA change-control regimes require attestation that
> someone reviewed the contract change.

Pilot's "they are templates!" framing:
- The 41 bundled YAML contracts are templates authored and reviewed by
  the OpenDQV core team. That IS honest attestation, not synthetic.
- Customer forks add THEIR organization's attestation when they go
  through their own approve workflow.

Fix:
- YAML loader (`_parse_contract_format`) now reads `approved_by` and
  `approved_at` from the YAML body (was only reading `proposed_by`).
- `record_version` now writes `proposed_by`, `proposed_at`,
  `approved_by`, `approved_at` to history (was only writing approved_by
  via explicit param).
- All 41 bundled YAMLs declare `proposed_by: opendqv-core-team` and
  `approved_by: opendqv-core-team` with timestamps.
- New `approved_at` column added to contract_history schema migration.

Recurrence: list_versions for any bundled contract returns populated
proposed_by + approved_by — no more null fields on the audit-replay
read surface.
"""



class TestBundledContractAttestation:
    def test_customer_list_versions_has_proposed_by(self, client, auth_headers):
        r = client.get("/api/v1/contracts/customer/versions", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        versions = body["versions"] if isinstance(body, dict) else body
        assert versions, "customer should have at least one history row"
        # The most-recent (active) row must carry the template attestation.
        active = [v for v in versions if v.get("status") == "active"]
        assert active, f"no active version: {versions}"
        latest = active[-1]
        assert latest.get("proposed_by") == "opendqv-core-team", (
            f"v2.3.20 P1.4 regression: customer.proposed_by null on bundled "
            f"exemplar. Got: {latest.get('proposed_by')!r}"
        )
        assert latest.get("approved_by") == "opendqv-core-team", (
            f"v2.3.20 P1.4 regression: customer.approved_by null on bundled "
            f"exemplar. Got: {latest.get('approved_by')!r}"
        )

    def test_mifid_list_versions_has_attestation(self, client, auth_headers):
        r = client.get(
            "/api/v1/contracts/mifid_transaction_report/versions",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        versions = body["versions"] if isinstance(body, dict) else body
        active = [v for v in versions if v.get("status") == "active"]
        assert active
        latest = active[-1]
        assert latest.get("proposed_by") == "opendqv-core-team"
        assert latest.get("approved_by") == "opendqv-core-team"

    def test_all_bundled_yamls_carry_template_attestation(self):
        """File-level invariant: every bundled YAML must declare
        proposed_by + approved_by. Catches a regression where a future
        contract addition forgets the attestation header."""
        import yaml
        from pathlib import Path

        contracts_dir = Path("opendqv/contracts")
        unattested = []
        for path in sorted(contracts_dir.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not raw or "contract" not in raw:
                continue
            c = raw["contract"]
            if not c.get("proposed_by") or not c.get("approved_by"):
                unattested.append(path.name)
        assert not unattested, (
            f"v2.3.20 P1.4: {len(unattested)} bundled contract(s) missing "
            f"template-level attestation: {unattested}. Every bundled "
            f"YAML must declare proposed_by + approved_by."
        )
