"""
v2.3.20 Cluster F (I-1, I-2, I-4) — mifid_transaction_report description honesty.

The Pilot's "they are templates!" framing on Cluster H applies to these
inside-view findings too:
- I-1 transaction_type lookup uses informal "buy"/"sell" pair (not RTS 22 codes)
- I-2 reviewed_by/review_date demanded as severity:error (NOT MiFIR/RTS 22 fields)
- I-4 buyer/seller_id_type values are starter taxonomy (not RTS 22 short codes)

These are TEMPLATE-LEVEL rules, not regulatory authority claims. The
N-5 description-honesty pattern shipped in v2.3.17 (LEI shape-only) and
v2.3.19 (MIC shape-only) extends here: keep the rules at severity:error
to teach customers what "good" looks like, but the error messages must
admit explicitly that the lookup values are starter taxonomies and that
customers must tailor for their actual ARM submission codes.
"""



class TestMifidTemplateRulesAreHonest:
    def test_buyer_id_type_states_starter_taxonomy(self, client, auth_headers):
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        assert r.status_code == 200
        rule = next(
            rr for rr in r.json()["rules"] if rr["name"] == "buyer_id_type_valid"
        )
        msg = rule["error_message"]
        assert "starter taxonomy" in msg, (
            f"buyer_id_type_valid error_message must admit it's a starter taxonomy "
            f"and that real RTS 22 uses different codes; got: {msg!r}"
        )

    def test_seller_id_type_states_starter_taxonomy(self, client, auth_headers):
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        rule = next(
            rr for rr in r.json()["rules"] if rr["name"] == "seller_id_type_valid"
        )
        assert "starter taxonomy" in rule["error_message"]

    def test_transaction_type_states_starter_taxonomy(self, client, auth_headers):
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        rule = next(
            rr for rr in r.json()["rules"] if rr["name"] == "transaction_type_valid"
        )
        msg = rule["error_message"]
        assert "starter taxonomy" in msg
        # Specifically must point customers at buy_sell_indicator as the
        # RTS 22 code field (so a customer reading this rule's failure
        # knows the platform is consistent with itself, not contradicting).
        assert "buy_sell_indicator" in msg, (
            f"transaction_type error_message should point to the RTS 22 "
            f"buy_sell_indicator field as the proper-code surface; got: {msg!r}"
        )

    def test_reviewed_by_states_template_level(self, client, auth_headers):
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        rule = next(
            rr for rr in r.json()["rules"] if rr["name"] == "reviewed_by_required"
        )
        msg = rule["error_message"]
        assert "TEMPLATE-LEVEL" in msg or "template" in msg.lower(), (
            f"reviewed_by_required error_message must explicitly state it is "
            f"TEMPLATE-LEVEL, not a MiFIR/RTS 22 field; got: {msg!r}"
        )
        assert "MiFIR" in msg or "RTS 22" in msg, (
            f"reviewed_by_required error_message must explicitly disclaim "
            f"the regulator framing; got: {msg!r}"
        )

    def test_review_date_states_template_level(self, client, auth_headers):
        r = client.get("/api/v1/contracts/mifid_transaction_report", headers=auth_headers)
        rule = next(
            rr for rr in r.json()["rules"] if rr["name"] == "review_date_required"
        )
        msg = rule["error_message"]
        assert "TEMPLATE-LEVEL" in msg or "template" in msg.lower()
        assert "MiFIR" in msg or "RTS 22" in msg
