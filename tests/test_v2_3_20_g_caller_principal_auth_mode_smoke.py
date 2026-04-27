"""
v2.3.20 Cluster G — caller_principal auth-mode smoke (recurrence test).

The Persona B 2026-04-27 outside reviewer flagged ``caller_principal:
"anonymous"`` on every validate response and asked in writing whether
the SDK/REST production path captures an authenticated principal. The
answer is yes — but yes-with-evidence is what the reviewer asked for.

This test smokes the AUTH_MODE=token path end-to-end:
1. Mint a real JWT PAT via ``security.auth.create_pat``.
2. POST /api/v1/validate with ``Authorization: Bearer <token>``.
3. Assert ``caller_principal`` equals the JWT ``sub`` claim, NOT
   ``"anonymous"``.

Recurrence guard: if a future change to ``get_current_user`` or to the
validate route's ``Depends(get_current_user)`` wiring drops the JWT
``sub`` capture (e.g. someone refactors auth and accidentally returns
the role string or a hardcoded "anonymous"), this test fails loudly
with a regulator-fidelity-impact message.
"""



class TestCallerPrincipalCapture:
    def test_jwt_sub_lands_in_caller_principal(self, client):
        """JWT ``sub`` claim must populate ``caller_principal`` on every
        validate response. Closes the v2.3.20 P1.6 reviewer ask."""
        from opendqv.security.auth import create_pat

        principal = "alice@bank.example.com"
        result = create_pat(principal, role="reader")
        token = result["token"]

        body = {
            "contract": "customer",
            "record": {"name": "Test", "age": 30, "email": "t@x.co"},
        }
        r = client.post(
            "/api/v1/validate?allow_draft=true",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        resp = r.json()
        assert resp["caller_principal"] == principal, (
            f"caller_principal must capture JWT sub claim ({principal!r}); "
            f"got {resp['caller_principal']!r}. Regulator-fidelity impact: "
            f"SoX/DORA/MiFIR change-of-write attestation requires the "
            f"caller_principal to be the authenticated identity, not the "
            f"caller-asserted agent_id and never 'anonymous' in token mode."
        )
        # And distinct from the caller-asserted agent_id (which is
        # spoofable; caller_principal is server-derived from the JWT).
        assert resp["caller_principal"] != "anonymous"

    def test_distinct_principals_yield_distinct_caller_principal(self, client):
        """Negative-control: two different JWT subs MUST land in distinct
        caller_principal values. Catches a regression where a future
        refactor accidentally hardcoded the value."""
        from opendqv.security.auth import create_pat

        token_a = create_pat("alice@bank.example.com", role="reader")["token"]
        token_b = create_pat("bob@bank.example.com", role="reader")["token"]

        body = {
            "contract": "customer",
            "record": {"name": "Test", "age": 30, "email": "t@x.co"},
        }
        r_a = client.post(
            "/api/v1/validate?allow_draft=true", json=body,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        r_b = client.post(
            "/api/v1/validate?allow_draft=true", json=body,
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert r_a.status_code == 200 and r_b.status_code == 200
        assert r_a.json()["caller_principal"] == "alice@bank.example.com"
        assert r_b.json()["caller_principal"] == "bob@bank.example.com"
        assert r_a.json()["caller_principal"] != r_b.json()["caller_principal"]
