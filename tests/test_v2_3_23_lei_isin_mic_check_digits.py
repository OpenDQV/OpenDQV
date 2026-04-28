"""
v2.3.23 round-3 #6 — LEI/ISIN check-digit verification + MIC registry lookup.

Persona B 2026-04-28 outside review #3: LEI/MIC/ISIN shape-only.

Sonnet pre-impl review (a0808af5a86013daf):
  - The engine ALREADY implements lei_mod97 and isin_mod11 in
    `_validate_checksum` (core/validator.py:218-296). The defect was
    purely contract-side: bundled mifid_transaction_report.yaml used
    shape-only regex rules instead of the existing checksum rule type.
    Fix: contract-only update — no engine code changes.
  - For MIC: ISO 10383 has no check digit; validation = registry
    membership. Ship ref/iso_10383_mic_codes.txt with major operating
    MICs and switch contract rule from regex to lookup.
  - GLEIF for known-valid LEI fixtures.
  - Honest CHANGELOG: sentinel/test LEIs that passed shape-only will
    now fail check-digit. Intended.

Test seeds (all real, public):
  - Valid LEIs (GLEIF golden-copy):
    - 529900T8BM49AURSDO55 (BNP Paribas)
    - 213800Z9OFRMVPH9X128 (Goldman Sachs International)
    - 7H6GLXDRUGQFU57RNE97 (JPMorgan Chase Bank N.A.)
  - Valid ISINs (publicly traded securities):
    - US0378331005 (Apple Inc. common stock)
    - GB0002634946 (BAE Systems)
    - DE000BASF111 (BASF SE)
  - Valid MICs (from our shipped ref):
    - XLON, XNAS, XNYS, XPAR
"""

import pytest
import yaml
from pathlib import Path


VALID_LEIS = [
    "529900T8BM49AURSDO55",  # BNP Paribas
    "7H6GLXDRUGQFU57RNE97",  # JPMorgan Chase Bank N.A.
    "W22LROWP2IHZNBB6K528",  # Goldman Sachs Group
    "HWUPKR0MPOU8FGXBT394",  # Microsoft Corporation
]
VALID_ISINS = [
    "US0378331005",  # Apple Inc.
    "GB0002634946",  # BAE Systems
    "DE000BASF111",  # BASF SE
]


# ── Engine-level: existing checksum implementations cover LEI / ISIN ───

class TestExistingChecksumCoversLeiIsin:
    """Engine already supports lei_mod97 and isin_mod11. Pin the
    behaviour so a future refactor doesn't lose it."""

    def test_lei_mod97_accepts_known_valid(self):
        from opendqv.core.validator import _validate_checksum
        for lei in VALID_LEIS:
            assert _validate_checksum(lei, "lei_mod97") is True, lei

    def test_lei_mod97_rejects_one_digit_flip(self):
        """Take a valid LEI, flip the last digit. Must fail."""
        from opendqv.core.validator import _validate_checksum
        for lei in VALID_LEIS:
            corrupted = lei[:-1] + ("0" if lei[-1] != "0" else "1")
            assert _validate_checksum(corrupted, "lei_mod97") is False, corrupted

    def test_lei_mod97_rejects_wrong_length(self):
        from opendqv.core.validator import _validate_checksum
        assert _validate_checksum("ABC123", "lei_mod97") is False
        assert _validate_checksum("A" * 21, "lei_mod97") is False

    def test_lei_mod97_rejects_lowercase_passthrough(self):
        """Engine upper-cases input; valid LEI must still validate."""
        from opendqv.core.validator import _validate_checksum
        assert _validate_checksum("529900t8bm49aursdo55", "lei_mod97") is True

    def test_isin_mod11_accepts_known_valid(self):
        from opendqv.core.validator import _validate_checksum
        for isin in VALID_ISINS:
            assert _validate_checksum(isin, "isin_mod11") is True, isin

    def test_isin_mod11_rejects_one_digit_flip(self):
        from opendqv.core.validator import _validate_checksum
        for isin in VALID_ISINS:
            corrupted = isin[:-1] + ("0" if isin[-1] != "0" else "1")
            assert _validate_checksum(corrupted, "isin_mod11") is False, corrupted

    def test_isin_mod11_rejects_wrong_length(self):
        from opendqv.core.validator import _validate_checksum
        assert _validate_checksum("US12345", "isin_mod11") is False


# ── MIC registry ref file ──────────────────────────────────────────────

class TestMicRegistryRefFile:
    REF = Path(
        "/home/sunny-sharma/OpenDQV/opendqv/contracts/ref/iso_10383_mic_codes.txt"
    )

    def test_ref_file_ships(self):
        assert self.REF.exists()

    def test_ref_file_contains_major_venues(self):
        codes = {
            line.strip().split()[0]
            for line in self.REF.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
        # Sanity: the major MICs must be present.
        for must_have in ("XLON", "XNAS", "XNYS", "XPAR", "XHKG", "XJPX",
                          "XSWX", "XETR", "XAMS", "XSES"):
            assert must_have in codes, f"missing {must_have}; got {len(codes)} codes"

    def test_ref_file_freshness_comment(self):
        text = self.REF.read_text(encoding="utf-8")
        assert "Snapshot date" in text or "snapshot" in text.lower(), (
            "MIC ref file must carry a freshness comment so operators "
            "know to refresh periodically."
        )


# ── Contract-level: mifid_transaction_report uses checksum + lookup ───

class TestMifidContractUsesCheckDigitRules:
    """The bundled mifid_transaction_report.yaml must use the checksum
    rule type for LEI/ISIN and lookup for MIC. No more shape-only regex
    on these identifiers."""

    @pytest.fixture
    def contract(self):
        path = Path(
            "/home/sunny-sharma/OpenDQV/opendqv/contracts/"
            "mifid_transaction_report.yaml"
        )
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_lei_rules_use_checksum_not_regex(self, contract):
        rules = contract["contract"]["rules"]
        # All LEI-validation rules must be checksum-typed (not regex shape-only).
        lei_validation_rules = [
            r for r in rules
            if r.get("name", "").endswith("_lei_valid")
        ]
        assert lei_validation_rules, "no LEI validation rules found"
        for r in lei_validation_rules:
            assert r.get("type") == "checksum", (
                f"v2.3.23 round-3: LEI validation rule {r['name']!r} "
                f"must use type=checksum, not type={r.get('type')!r}. "
                f"Shape-only regex was the v2.3.22 leak the reviewer "
                f"flagged across 3 review rounds."
            )
            assert r.get("checksum_algorithm") == "lei_mod97", (
                f"LEI rule {r['name']!r} must use checksum_algorithm=lei_mod97."
            )

    def test_isin_rule_uses_checksum_not_regex(self, contract):
        rules = contract["contract"]["rules"]
        isin_rules = [r for r in rules if "isin" in r.get("name", "").lower()
                      and r.get("name", "").endswith("_valid")]
        assert isin_rules, "no ISIN validation rule found"
        for r in isin_rules:
            assert r.get("type") == "checksum", (
                f"ISIN rule {r['name']!r} must use type=checksum"
            )
            assert r.get("checksum_algorithm") == "isin_mod11"

    def test_mic_rule_uses_lookup_not_regex(self, contract):
        rules = contract["contract"]["rules"]
        mic_rules = [r for r in rules if r.get("field") == "venue_mic"
                     and r.get("name", "").endswith("_valid")]
        assert mic_rules, "no MIC validation rule found"
        for r in mic_rules:
            assert r.get("type") == "lookup", (
                f"MIC rule {r['name']!r} must use type=lookup against "
                f"ref/iso_10383_mic_codes.txt (was: shape-only regex)."
            )
            assert "iso_10383" in r.get("lookup_file", ""), (
                "MIC rule must point at iso_10383_mic_codes.txt"
            )

    def test_no_v24_deferral_language_remains_on_lei_isin_mic(self, contract):
        """The v2.3.22 contract carried 'shape only — full ISO 17442
        mod-97 check-digit verification is a v2.4 capability' in the
        error message. v2.3.23 round-3: that deferral is closed."""
        rules = contract["contract"]["rules"]
        for r in rules:
            field = r.get("field", "")
            if field.endswith("_lei") or field == "venue_mic" or "isin" in field:
                msg = r.get("error_message", "")
                assert "v2.4 capability" not in msg, (
                    f"v2.3.23 round-3: rule {r.get('name')!r} still "
                    f"carries v2.4-deferral language in error message — "
                    f"check-digit verification ships in v2.3.23. "
                    f"Got: {msg!r}"
                )


# ── End-to-end: live engine accepts known-valid + rejects sentinel ────

class TestMifidContractLiveValidation:
    """Validate against the live registry. Known-valid LEI/ISIN/MIC
    must pass. Shape-correct sentinels must fail."""

    def _valid_record(self):
        return {
            "transaction_reference_number": "TR-2026-04-28-0001",
            "execution_timestamp": "2026-04-28T14:00:00Z",
            "reporting_firm_lei": VALID_LEIS[0],
            "executing_entity_lei": VALID_LEIS[1],
            "venue_mic": "XLON",
            "instrument_isin": VALID_ISINS[0],
            "buyer_id_type": "lei",
            "buyer_id": VALID_LEIS[2],
            "seller_id_type": "lei",
            "seller_id": VALID_LEIS[0],
            "price": 123.45,
            "quantity": 1000,
            "currency": "GBP",
        }

    def test_known_valid_record_passes_check_digits(self, client, auth_headers):
        resp = client.post(
            "/api/v1/validate",
            json={"contract": "mifid_transaction_report",
                  "record": self._valid_record()},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Check-digit-related rules must not fail.
        cd_rule_names = {
            "reporting_firm_lei_valid", "executing_entity_lei_valid",
            "instrument_isin_valid", "venue_mic_valid",
            "buyer_id_lei_valid", "seller_id_lei_valid",
        }
        failed_cd = [
            e for e in body.get("errors", [])
            if e.get("rule") in cd_rule_names
        ]
        assert not failed_cd, (
            f"v2.3.23 round-3: known-valid LEIs/ISINs/MICs must pass "
            f"check-digit validation. Failed: {failed_cd}"
        )

    def test_shape_correct_sentinel_lei_now_fails(self, client, auth_headers):
        """The reviewer's exact concern: 'sentinel/typo LEIs that match
        the shape will pass shape-only validation but fail at GLEIF
        lookup.' v2.3.23 round-3 closes that gap — the engine itself
        catches them."""
        record = self._valid_record()
        # 'AAAAAAAAAAAAAAAAAA00' is shape-correct (18 alphanumeric + 2
        # digits) but is NOT a valid LEI under mod-97-10.
        record["reporting_firm_lei"] = "AAAAAAAAAAAAAAAAAA00"
        resp = client.post(
            "/api/v1/validate",
            json={"contract": "mifid_transaction_report", "record": record},
            headers=auth_headers,
        )
        body = resp.json()
        failed_names = [e.get("rule") for e in body.get("errors", [])]
        assert "reporting_firm_lei_valid" in failed_names, (
            f"v2.3.23 round-3: shape-correct sentinel LEI must now fail "
            f"at the engine boundary, not at the ARM. Got errors: "
            f"{body.get('errors', [])}"
        )

    def test_shape_correct_sentinel_isin_now_fails(self, client, auth_headers):
        record = self._valid_record()
        # Shape-correct (US + 9 alphanumeric + 1 digit) but invalid Luhn.
        record["instrument_isin"] = "US1234567890"
        resp = client.post(
            "/api/v1/validate",
            json={"contract": "mifid_transaction_report", "record": record},
            headers=auth_headers,
        )
        body = resp.json()
        failed_names = [e.get("rule") for e in body.get("errors", [])]
        assert "instrument_isin_valid" in failed_names, (
            f"v2.3.23 round-3: shape-correct sentinel ISIN must now fail. "
            f"Got: {body.get('errors')}"
        )

    def test_unknown_mic_now_fails(self, client, auth_headers):
        record = self._valid_record()
        # Shape-correct (4 letters) but not in the bundled ISO 10383 list.
        record["venue_mic"] = "ZZZZ"
        resp = client.post(
            "/api/v1/validate",
            json={"contract": "mifid_transaction_report", "record": record},
            headers=auth_headers,
        )
        body = resp.json()
        failed_names = [e.get("rule") for e in body.get("errors", [])]
        assert "venue_mic_valid" in failed_names, (
            f"v2.3.23 round-3: unknown MIC must fail registry lookup. "
            f"Got: {body.get('errors')}"
        )
