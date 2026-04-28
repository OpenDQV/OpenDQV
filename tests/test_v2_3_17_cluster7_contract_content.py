"""
v2.3.17 Cluster 7 — contract content honesty (mifid_transaction_report).

Q13 (Pilot decision: option (a) RTS 25 µs UTC):
  ``execution_timestamp_format`` is now a regex enforcing
  ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (microsecond UTC). Closes the
  four-way contradiction Persona B reported in N-1: the rule-required
  error message claimed microsecond granularity, the format rule
  rejected microseconds, the explainer said YYYY-MM-DD, JSON Schema
  exported ``format: date``. All four surfaces now align on the strict
  RTS 25 form.

Q14 (Pilot decision: hard path — three rules):
  - ``trade_date_matches_execution_date`` — regex ensures trade_date is
    a valid ISO date; the doc text references the T+0 invariant per
    RTS 22 Annex Table 2 + ESMA Q&A TR 9.1.
  - ``trade_date_not_in_future`` — compare against today (lte).
  - ``execution_timestamp_not_in_future`` — compare against now (lte).
  Closes Persona B's N-4: ``trade_date: 2030-01-01`` previously passed.

N-5 description honesty:
  LEI rules now state explicitly that the regex enforces SHAPE only;
  full ISO 17442 mod-97 check-digit verification is a v2.4 capability.
  Closes Persona B's "description over-claims what the rule does"
  finding without inventing a checksum capability we don't yet have.
"""



# ── Q13: timestamp format ─────────────────────────────────────────────

class TestMifidTimestampFormat:
    def test_rts25_microsecond_utc_passes(self, client, auth_headers):
        body = self._happy_record()
        body["record"]["execution_timestamp"] = "2026-04-27T14:30:15.123456Z"
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        # No format error on execution_timestamp_format specifically
        errors = r.json().get("errors", [])
        format_errs = [e for e in errors if e.get("rule") == "execution_timestamp_format"]
        assert not format_errs, f"RTS 25 µs UTC must pass format rule: {format_errs}"

    def test_seconds_only_rejected(self, client, auth_headers):
        body = self._happy_record()
        body["record"]["execution_timestamp"] = "2026-04-27T14:30:15Z"
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        body_json = r.json()
        format_errs = [e for e in body_json.get("errors", []) if e.get("rule") == "execution_timestamp_format"]
        assert format_errs, \
            f"v2.3.17 Q13: seconds-only timestamp must fail RTS 25 µs format. Got errors: {body_json.get('errors')}"

    def test_date_only_rejected(self, client, auth_headers):
        body = self._happy_record()
        body["record"]["execution_timestamp"] = "2026-04-27"
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        format_errs = [e for e in r.json().get("errors", []) if e.get("rule") == "execution_timestamp_format"]
        assert format_errs, "date-only must fail RTS 25 µs format"

    def test_milliseconds_rejected(self, client, auth_headers):
        body = self._happy_record()
        body["record"]["execution_timestamp"] = "2026-04-27T14:30:15.123Z"
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        format_errs = [e for e in r.json().get("errors", []) if e.get("rule") == "execution_timestamp_format"]
        assert format_errs, "millisecond precision must fail RTS 25 µs format (need 6 fractional digits)"

    def test_offset_not_z_rejected(self, client, auth_headers):
        body = self._happy_record()
        body["record"]["execution_timestamp"] = "2026-04-27T14:30:15.123456+01:00"
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        format_errs = [e for e in r.json().get("errors", []) if e.get("rule") == "execution_timestamp_format"]
        assert format_errs, "non-UTC offset must fail RTS 25 µs format (Z required)"

    def _happy_record(self):
        return {
            "contract": "mifid_transaction_report",
            "record": {
                "transaction_reference": "TXN-2026-0001",
                # Use yesterday so the test passes regardless of run time
                # (today's record could fail "not in future" if test runs
                # before the record's wall-clock time on the same day).
                "execution_timestamp": "2026-04-26T10:00:00.000000Z",
                "trade_date": "2026-04-26",
                "reporting_firm_lei": "529900T8BM49AURSDO55",
                "executing_entity_lei": "529900T8BM49AURSDO55",
                "venue_mic": "XLON",
                "instrument_isin": "GB00B03MLX29",
                "buyer_id_type": "lei",
                "buyer_id": "529900T8BM49AURSDO55",
                "seller_id_type": "lei",
                "seller_id": "529900T8BM49AURSDO55",
                "price": 100.5,
                "price_type": "monetary",
                "currency": "GBP",
                "quantity": 1000,
                "quantity_type": "units",
                "buy_sell_indicator": "BUYI",
                "investment_decision_within_firm": "529900T8BM49AURSDO55",
                "execution_within_firm": "529900T8BM49AURSDO55",
                "transaction_type": "T",
            },
        }


# ── Q14: temporal rules ───────────────────────────────────────────────

class TestMifidTemporalRules:
    def test_future_trade_date_rejected(self, client, auth_headers):
        """The original Persona B finding — trade_date 2030-01-01 used
        to pass; must now fail trade_date_not_in_future."""
        body = TestMifidTimestampFormat()._happy_record()
        body["record"]["trade_date"] = "2030-01-01"
        body["record"]["execution_timestamp"] = "2030-01-01T10:00:00.000000Z"
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        errors = r.json().get("errors", [])
        future_errs = [e for e in errors if e.get("rule") in (
            "trade_date_not_in_future", "execution_timestamp_not_in_future",
        )]
        assert future_errs, \
            f"future-dated record (self-consistent 2030/2030) must fail forwards-bound rules. Got errors: {errors}"

    def test_present_record_passes_temporal_rules(self, client, auth_headers):
        body = TestMifidTimestampFormat()._happy_record()
        # happy record uses 2026-04-27 — today-ish, definitely not future
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        errors = r.json().get("errors", [])
        temporal_errs = [
            e for e in errors
            if e.get("rule") in (
                "trade_date_not_in_future",
                "execution_timestamp_not_in_future",
                "trade_date_matches_execution_date",
            )
        ]
        assert not temporal_errs, \
            f"present-day record should pass temporal rules; got: {temporal_errs}"


# ── N-5 → v2.3.23 round-3: LEI / MIC validation upgraded from shape-only ──
# to full check-digit / registry verification.
#
# v2.3.17 cluster 7 introduced the SHAPE-ONLY honesty pattern: error
# messages explicitly stated they enforced shape only and check-digit
# verification was deferred to v2.4. v2.3.23 round-3 closes that gap —
# the engine's existing checksum (lei_mod97 / isin_mod11) and lookup
# (ref/iso_10383_mic_codes.txt) rule types are now wired into the
# bundled mifid_transaction_report contract. The historical honesty
# language is no longer applicable; the new contract MUST not carry it.

class TestLeiCheckDigitVerificationShipped:
    def test_lei_rules_use_checksum_not_shape_only_regex(self, client, auth_headers):
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        assert r.status_code == 200
        rules = r.json().get("rules", [])
        lei_rules = [
            rr for rr in rules
            if rr["name"] in ("reporting_firm_lei_valid", "executing_entity_lei_valid")
        ]
        assert lei_rules, (
            "LEI validation rules must be present under their v2.3.23 names "
            "(*_lei_valid). Old shape-only rule names (*_lei_format) were "
            "renamed when the contract upgraded from regex shape to checksum "
            "verification."
        )
        for rule in lei_rules:
            assert rule.get("type") == "checksum", (
                f"Rule {rule['name']!r} must use type=checksum (was: regex "
                f"shape-only). v2.3.23 round-3 closed the v2.4 deferral."
            )
            msg = rule.get("error_message", "")
            assert "v2.4 capability" not in msg, (
                f"Rule {rule['name']!r} still carries v2.4-deferral language. "
                f"Check-digit verification ships in v2.3.23. Got: {msg!r}"
            )


class TestMicRegistryLookupShipped:
    """v2.3.23 round-3: shape-only regex on MIC upgraded to lookup
    against the bundled ref/iso_10383_mic_codes.txt registry (starter
    subset of major operating MICs). Operators with broader needs drop
    a complete extract at $OPENDQV_CONTRACTS_DIR/ref."""

    def test_mic_rule_uses_lookup_not_shape_only_regex(self, client, auth_headers):
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        assert r.status_code == 200
        rules = r.json().get("rules", [])
        mic_rule = next(
            (rr for rr in rules if rr["name"] == "venue_mic_valid"), None,
        )
        assert mic_rule is not None, (
            "venue_mic_valid rule must exist (renamed from venue_mic_format "
            "when the contract upgraded from regex shape to registry lookup)."
        )
        assert mic_rule.get("type") == "lookup", (
            f"venue_mic_valid must use type=lookup (was: regex shape-only). "
            f"Got: {mic_rule.get('type')!r}"
        )
        assert "iso_10383" in mic_rule.get("lookup_file", ""), (
            "venue_mic_valid must point at iso_10383_mic_codes.txt"
        )
        msg = mic_rule.get("error_message", "")
        assert "v2.4 capability" not in msg, (
            f"venue_mic_valid still carries v2.4-deferral language. "
            f"Registry lookup ships in v2.3.23. Got: {msg!r}"
        )
