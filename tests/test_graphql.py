"""Tests for the GraphQL API.

The suite runs with AUTH_MODE=token (see conftest), so every /graphql request
must carry a Bearer token. Before the CRT175 discovery fix the GraphQL router
had no auth dependency, so these requests succeeded unauthenticated — an
unlogged, uncapped bypass of the token wall the REST surface enforces. The
tests below now authenticate, and TestGraphQLSecurityParity guards the fix.
"""

import opendqv.config as config


class TestGraphQLQueries:
    def test_list_contracts(self, client, auth_headers):
        query = '{ contracts { name version description ruleCount } }'
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data["contracts"]) > 0
        assert any(c["name"] == "customer" for c in data["contracts"])

    def test_get_contract_detail(self, client, auth_headers):
        query = '{ contract(name: "customer") { name version rules { name type field severity } contexts } }'
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]["contract"]
        assert data["name"] == "customer"
        assert len(data["rules"]) > 0

    def test_contract_not_found(self, client, auth_headers):
        query = '{ contract(name: "nonexistent") { name } }'
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"]["contract"] is None


class TestGraphQLMutations:
    def test_validate_single(self, client, auth_headers):
        query = '''
        mutation {
            validate(
                record: {email: "test@example.com", age: 25, name: "Alice", id: "123", phone: "+1234567890", balance: 100, score: 85, date: "2024-01-15", username: "alice_w", password: "securepass1"},
                contract: "customer"
            ) {
                valid
                errors { field rule message severity }
                warnings { field rule message severity }
                contract
                version
            }
        }
        '''
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]["validate"]
        assert data["valid"] is True
        assert data["contract"] == "customer"

    def test_validate_invalid(self, client, auth_headers):
        query = '''
        mutation {
            validate(
                record: {email: "bad", age: -5, name: ""},
                contract: "customer"
            ) {
                valid
                errors { field rule message }
            }
        }
        '''
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]["validate"]
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_validate_with_record_id(self, client, auth_headers):
        query = '''
        mutation {
            validate(
                record: {email: "a@b.com"},
                contract: "customer",
                recordId: "sf-12345"
            ) {
                valid
                recordId
            }
        }
        '''
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        data = r.json()["data"]["validate"]
        assert data["recordId"] == "sf-12345"

    def test_validate_batch(self, client, auth_headers):
        query = '''
        mutation {
            validateBatch(
                records: [{email: "a@b.com", age: 25}, {email: "bad", age: -5}],
                contract: "customer"
            ) {
                summary { total passed failed errorCount warningCount }
                results { index valid errors { field message } }
                contract
            }
        }
        '''
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()["data"]["validateBatch"]
        assert data["summary"]["total"] == 2
        assert data["summary"]["failed"] > 0
        assert len(data["results"]) == 2

    def test_validate_contract_not_found(self, client, auth_headers):
        query = '''
        mutation {
            validate(record: {}, contract: "nonexistent") {
                valid
                errors { field message }
            }
        }
        '''
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        data = r.json()["data"]["validate"]
        assert data["valid"] is False
        assert any("not found" in e["message"] for e in data["errors"])


class TestGraphQLSecurityParity:
    """Recurrence guards for the CRT175 GraphQL-bypass fix.

    Each test pins one leg of the security parity GraphQL must hold with the
    REST /validate surface: auth required in token mode, batch size capped,
    empty batch rejected. If a future refactor drops the router dependency or
    the in-resolver cap, one of these fails.
    """

    def _batch_query(self, records_literal: str) -> str:
        return (
            "mutation { validateBatch(records: "
            + records_literal
            + ', contract: "customer") { summary { total } } }'
        )

    def test_graphql_requires_auth_in_token_mode(self, client):
        # No Authorization header — the router-level get_current_user
        # dependency must reject before any resolver runs.
        query = '{ contracts { name } }'
        r = client.post("/graphql", json={"query": query})
        assert r.status_code == 401

    def test_graphql_mutation_requires_auth_in_token_mode(self, client):
        query = 'mutation { validate(record: {email: "a@b.com"}, contract: "customer") { valid } }'
        r = client.post("/graphql", json={"query": query})
        assert r.status_code == 401

    def test_batch_rejects_empty(self, client, auth_headers):
        r = client.post(
            "/graphql",
            json={"query": self._batch_query("[]")},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert (body.get("data") or {}).get("validateBatch") is None
        assert any("empty" in e["message"].lower() for e in body["errors"])

    def test_batch_rejects_oversized(self, client, auth_headers, monkeypatch):
        # Shrink the cap so we don't have to build 10k records.
        monkeypatch.setattr(config, "MAX_BATCH_ROWS", 3)
        records = "[" + ", ".join('{email: "a@b.com"}' for _ in range(4)) + "]"
        r = client.post(
            "/graphql",
            json={"query": self._batch_query(records)},
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert (body.get("data") or {}).get("validateBatch") is None
        assert any("exceeds the maximum" in e["message"] for e in body["errors"])

    def test_batch_at_cap_is_accepted(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(config, "MAX_BATCH_ROWS", 3)
        records = "[" + ", ".join('{email: "a@b.com"}' for _ in range(3)) + "]"
        r = client.post(
            "/graphql",
            json={"query": self._batch_query(records)},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["data"]["validateBatch"]["summary"]["total"] == 3

    def test_batch_rejects_non_list_records(self, client, auth_headers):
        """CRT175 #4 (Sonnet): a non-list JSON payload must be rejected cleanly,
        not crash in DataFrame construction with a masked 'unexpected error'."""
        query = (
            'mutation { validateBatch(records: {email: "a@b.com"}, '
            'contract: "customer") { summary { total } } }'
        )
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert (body.get("data") or {}).get("validateBatch") is None
        assert any("must be a list" in e["message"] for e in body["errors"])

    def test_alias_amplification_is_capped(self, client, auth_headers):
        """CRT175 #3 (Sonnet): aliasing many root mutation fields in one request
        must be rejected before execution, closing the N×MAX_BATCH_ROWS DoS."""
        aliases = " ".join(
            f'a{i}: validateBatch(records: [{{email: "a@b.com"}}], contract: "customer") {{ summary {{ total }} }}'
            for i in range(15)
        )
        r = client.post(
            "/graphql", json={"query": "mutation { " + aliases + " }"}, headers=auth_headers
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("data") is None
        assert any("exceeding the maximum" in e["message"] for e in body["errors"])

    def test_alias_amplification_via_fragments_is_capped(self, client, auth_headers):
        """CRT175 Sonnet blind re-verify: hiding the aliased calls inside named
        fragments (`{ ...f0 ...f1 }`) must NOT evade the cap. The rule must
        count through fragment spreads, not just direct root fields."""
        frags = " ".join(
            f'fragment f{i} on Mutation {{ a{i}: validateBatch(records: [{{email: "a@b.com"}}], contract: "customer") {{ summary {{ total }} }} }}'
            for i in range(15)
        )
        spreads = " ".join(f"...f{i}" for i in range(15))
        query = "mutation { " + spreads + " } " + frags
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body.get("data") is None
        assert any("exceeding the maximum" in e["message"] for e in body["errors"])

    def test_alias_amplification_via_inline_fragments_is_capped(self, client, auth_headers):
        inline = " ".join(
            f'... on Mutation {{ a{i}: validateBatch(records: [{{email: "a@b.com"}}], contract: "customer") {{ summary {{ total }} }} }}'
            for i in range(15)
        )
        r = client.post(
            "/graphql", json={"query": "mutation { " + inline + " }"}, headers=auth_headers
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("data") is None
        assert any("exceeding the maximum" in e["message"] for e in body["errors"])

    def test_normal_multi_field_query_still_allowed(self, client, auth_headers):
        """A handful of root fields (well under the cap) must still work."""
        query = (
            'mutation { a: validate(record: {email: "a@b.com"}, contract: "customer") { valid } '
            'b: validate(record: {email: "c@d.com"}, contract: "customer") { valid } }'
        )
        r = client.post("/graphql", json={"query": query}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["data"]["a"]["valid"] is not None
