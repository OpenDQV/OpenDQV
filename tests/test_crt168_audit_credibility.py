"""CRT168 — audit credibility integration tests.

Covers the v2.2.6 audit-trail tightening that emerged from the
2026-04-25 external regulator-facing review:

- P1.1: every validate response carries a server-generated `event_id`
        (UUID v7, RFC 9562). Persisted on the audit row in `quality_stats`.
- P1.2: GET /api/v1/contracts/{name} accepts `?hash=<contract_hash>`,
        returning the exact historical contract version that produced
        that hash on a prior validate response — point-in-time retrieval.
"""

import sqlite3
import uuid

import opendqv.config as config


def _is_uuid_v7(s: str) -> bool:
    try:
        u = uuid.UUID(s)
    except (ValueError, TypeError):
        return False
    return u.version == 7 and ((u.int >> 62) & 0x3) == 0b10


class TestEventIdOnSingleValidate:
    def test_event_id_present_and_uuid_v7(self, client, auth_headers):
        body = {"record": {"email": "alice@example.com"}, "contract": "customer"}
        r = client.post("/api/v1/validate", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert "event_id" in data
        assert _is_uuid_v7(data["event_id"])

    def test_event_ids_are_unique_across_calls(self, client, auth_headers):
        body = {"record": {"email": "alice@example.com"}, "contract": "customer"}
        ids = set()
        for _ in range(10):
            ids.add(client.post("/api/v1/validate", json=body, headers=auth_headers).json()["event_id"])
        assert len(ids) == 10

    def test_record_id_still_echoed_alongside_event_id(self, client, auth_headers):
        body = {
            "record": {"email": "alice@example.com"},
            "contract": "customer",
            "record_id": "caller-correlation-9001",
        }
        data = client.post("/api/v1/validate", json=body, headers=auth_headers).json()
        assert data["record_id"] == "caller-correlation-9001"
        assert data["event_id"] != data["record_id"]
        assert _is_uuid_v7(data["event_id"])


class TestEventIdOnBatchValidate:
    def test_batch_response_carries_event_id_and_per_record_event_ids(self, client, auth_headers):
        body = {
            "records": [
                {"email": "a@b.com"},
                {"email": "c@d.com"},
                {"email": "e@f.com"},
            ],
            "contract": "customer",
        }
        r = client.post("/api/v1/validate/batch", json=body, headers=auth_headers)
        assert r.status_code == 200
        data = r.json()

        assert _is_uuid_v7(data["event_id"])

        per_record = [item["event_id"] for item in data["results"]]
        assert len(per_record) == 3
        for eid in per_record:
            assert _is_uuid_v7(eid)
        assert len(set(per_record)) == 3
        assert data["event_id"] not in per_record


class TestEventIdPersistedToSqlite:
    def test_event_id_written_to_quality_stats_row(self, client, auth_headers):
        body = {"record": {"email": "z@z.com"}, "contract": "customer"}
        data = client.post("/api/v1/validate", json=body, headers=auth_headers).json()
        event_id = data["event_id"]

        with sqlite3.connect(config.DB_PATH) as conn:
            row = conn.execute(
                "SELECT contract_name FROM quality_stats WHERE event_id = ?",
                (event_id,),
            ).fetchone()

        assert row is not None, "event_id from response was not persisted to quality_stats"
        assert row[0] == "customer"


class TestGetContractByHash:
    def test_hash_lookup_returns_matching_version(self, client, auth_headers):
        body = {"record": {"email": "a@b.com"}, "contract": "customer"}
        validate_resp = client.post("/api/v1/validate", json=body, headers=auth_headers).json()
        contract_hash = validate_resp["contract_hash"]
        assert contract_hash, "validate response must include contract_hash"

        r = client.get(f"/api/v1/contracts/customer?hash={contract_hash}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["name"] == "customer"
        assert detail["version"] == validate_resp["version"]
        assert len(detail["rules"]) > 0

    def test_hash_lookup_unknown_hash_returns_404(self, client, auth_headers):
        bogus = "0" * 64
        r = client.get(f"/api/v1/contracts/customer?hash={bogus}")
        assert r.status_code == 404

    def test_hash_takes_precedence_over_version(self, client, auth_headers):
        body = {"record": {"email": "a@b.com"}, "contract": "customer"}
        contract_hash = client.post("/api/v1/validate", json=body, headers=auth_headers).json()["contract_hash"]
        r = client.get(f"/api/v1/contracts/customer?version=does-not-exist&hash={contract_hash}")
        assert r.status_code == 200
        assert r.json()["name"] == "customer"
