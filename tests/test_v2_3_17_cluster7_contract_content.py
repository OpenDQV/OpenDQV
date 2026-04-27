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


# ── N-5: LEI description honesty ──────────────────────────────────────

class TestLeiDescriptionHonesty:
    def test_lei_error_messages_state_shape_only(self, client, auth_headers):
        # Get the contract via REST and assert error_messages no longer
        # claim full ISO 17442 verification (only shape).
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        assert r.status_code == 200
        rules = r.json().get("rules", [])
        lei_rules = [
            r for r in rules
            if r["name"] in ("reporting_firm_lei_format", "executing_entity_lei_format")
        ]
        assert lei_rules, "could not find LEI format rules"
        for rule in lei_rules:
            msg = rule.get("error_message", "")
            assert "shape" in msg.lower() or "v2.4" in msg.lower(), (
                f"Rule {rule['name']!r} error_message must explicitly state it enforces "
                f"SHAPE only (no checksum); got: {msg!r}"
            )
            # Must NOT claim "check digits" verification — that's the over-claim.
            assert "check digits" not in msg, (
                f"Rule {rule['name']!r} error_message still claims 'check digits' — "
                f"that implies mod-97 verification which is a v2.4 capability. "
                f"Got: {msg!r}"
            )


class TestMicDescriptionHonesty:
    """v2.3.19 I-3 (inside-view, Sonnet-pushed): same N-5 treatment for the
    MIC rule. The pattern ``^[A-Z]{4}$`` enforces shape only — a sentinel
    code like ``XYZA`` matches the shape but is not a real ISO 10383 MIC.
    The error message must be honest about this; full list lookup is v2.4.
    """

    def test_mic_error_message_states_shape_only(self, client, auth_headers):
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        assert r.status_code == 200
        rules = r.json().get("rules", [])
        mic_rule = next(
            (rr for rr in rules if rr["name"] == "venue_mic_format"), None,
        )
        assert mic_rule is not None, "venue_mic_format rule must exist"
        msg = mic_rule.get("error_message", "")
        assert "shape" in msg.lower() or "v2.4" in msg.lower(), (
            f"venue_mic_format error_message must state SHAPE-only honesty "
            f"(same N-5 treatment as LEI rules); got: {msg!r}"
        )
