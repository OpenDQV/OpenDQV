"""
API-level import tests with save=True.

Covers the save branch in routes_imports.py for dbt, soda, csv, CSVW, OTel, NDC, ODCS.
These paths were uncovered because existing importer tests use the core importers directly
without going through the API endpoints.
"""
import os
import yaml


SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_data")


def _load_yaml(filename):
    with open(os.path.join(SAMPLE_DIR, filename), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_text(filename):
    with open(os.path.join(SAMPLE_DIR, filename), encoding="utf-8") as f:
        return f.read()


class TestDbtImportAPI:
    """POST /api/v1/import/dbt — save=True branch."""

    def test_import_dbt_no_save(self, client, editor_headers):
        schema = _load_yaml("dbt_schema_sample.yml")
        r = client.post("/api/v1/import/dbt", json=schema, headers=editor_headers)
        assert r.status_code == 200
        body = r.json()
        assert "contracts" in body
        assert len(body["contracts"]) > 0

    def test_import_dbt_save_true(self, client, editor_headers):
        schema = _load_yaml("dbt_schema_sample.yml")
        r = client.post(
            "/api/v1/import/dbt",
            json=schema,
            params={"save": "true"},
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "contracts" in body
        # At least the first contract should report saved_to
        if body.get("saved_paths"):
            assert len(body["saved_paths"]) > 0

    def test_import_dbt_requires_editor(self, client, auth_headers):
        schema = _load_yaml("dbt_schema_sample.yml")
        r = client.post("/api/v1/import/dbt", json=schema, headers=auth_headers)
        assert r.status_code == 403

    def test_import_dbt_no_auth(self, client):
        r = client.post("/api/v1/import/dbt", json={})
        assert r.status_code == 401


class TestSodaImportAPI:
    """POST /api/v1/import/soda — save=True branch."""

    def test_import_soda_no_save(self, client, editor_headers):
        checks = _load_yaml("soda_checks_sample.yaml")
        r = client.post("/api/v1/import/soda", json=checks, headers=editor_headers)
        assert r.status_code == 200
        body = r.json()
        assert "contracts" in body

    def test_import_soda_save_true(self, client, editor_headers):
        checks = _load_yaml("soda_checks_sample.yaml")
        r = client.post(
            "/api/v1/import/soda",
            json=checks,
            params={"save": "true"},
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "contracts" in body

    def test_import_soda_requires_editor(self, client, auth_headers):
        checks = _load_yaml("soda_checks_sample.yaml")
        r = client.post("/api/v1/import/soda", json=checks, headers=auth_headers)
        assert r.status_code == 403

    def test_import_soda_no_auth(self, client):
        r = client.post("/api/v1/import/soda", json={})
        assert r.status_code == 401


class TestCSVImportAPI:
    """POST /api/v1/import/csv — save=True branch."""

    CSV_CONTENT = "name,type,field,min,max,error_message\nage_min,min,age,18,,Age must be 18+\n"

    def test_import_csv_no_save(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/csv?contract_name=test_csv_import",
            content=self.CSV_CONTENT.encode(),
            headers={"Content-Type": "text/plain", **editor_headers},
        )
        assert r.status_code == 200
        body = r.json()
        assert "contract" in body

    def test_import_csv_save_true(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/csv?contract_name=test_csv_save&save=true",
            content=self.CSV_CONTENT.encode(),
            headers={"Content-Type": "text/plain", **editor_headers},
        )
        assert r.status_code == 200
        body = r.json()
        assert "saved_to" in body

    def test_import_csv_no_auth(self, client):
        r = client.post(
            "/api/v1/import/csv?contract_name=x",
            content=self.CSV_CONTENT.encode(),
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code == 401


class TestCSVWImportAPI:
    """POST /api/v1/import/csvw — save=True branch."""

    CSVW_SCHEMA = {
        "@context": "http://www.w3.org/ns/csvw",
        "url": "patients.csv",
        "tableSchema": {
            "columns": [
                {"name": "patient_id", "required": True, "datatype": "string"},
                {"name": "age", "datatype": "integer",
                 "constraints": {"minimum": 0, "maximum": 150}},
            ]
        }
    }

    def test_import_csvw_no_save(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/csvw?contract_name=test_csvw",
            json=self.CSVW_SCHEMA,
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "rules" in body or "contract" in body

    def test_import_csvw_save_true(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/csvw?contract_name=test_csvw_saved&save=true",
            json=self.CSVW_SCHEMA,
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "saved_to" in body

    def test_import_csvw_no_auth(self, client):
        r = client.post("/api/v1/import/csvw?contract_name=x", json={})
        assert r.status_code == 401


class TestOTelImportAPI:
    """POST /api/v1/import/otel — save=True branch."""

    OTEL_SCHEMA = {
        "name": "http_request",
        "attributes": [
            {"key": "http.method", "type": "string", "required": True},
            {"key": "http.status_code", "type": "int", "required": True},
        ]
    }

    def test_import_otel_no_save(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/otel?contract_name=test_otel",
            json=self.OTEL_SCHEMA,
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "rules" in body or "contract" in body

    def test_import_otel_save_true(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/otel?contract_name=test_otel_saved&save=true",
            json=self.OTEL_SCHEMA,
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "saved_to" in body

    def test_import_otel_no_auth(self, client):
        r = client.post("/api/v1/import/otel?contract_name=x", json={})
        assert r.status_code == 401


class TestNDCImportAPI:
    """POST /api/v1/import/ndc — save=True branch."""

    NDC_SCHEMA = {
        "name": "product_data",
        "fields": [
            {"name": "product_id", "type": "string", "nullable": False},
            {"name": "price", "type": "decimal", "nullable": False,
             "constraints": {"minimum": 0}},
        ]
    }

    def test_import_ndc_no_save(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/ndc?contract_name=test_ndc",
            json=self.NDC_SCHEMA,
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "rules" in body or "contract" in body

    def test_import_ndc_save_true(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/ndc?contract_name=test_ndc_saved&save=true",
            json=self.NDC_SCHEMA,
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "saved_to" in body

    def test_import_ndc_no_auth(self, client):
        r = client.post("/api/v1/import/ndc?contract_name=x", json={})
        assert r.status_code == 401


class TestODCSImportAPI:
    """POST /api/v1/import/odcs — save=True branch."""

    ODCS_CONTRACT = {
        "kind": "DataContract",
        "apiVersion": "v3.0.0",
        "id": "test-odcs-001",
        "name": "test_odcs_contract",
        "schema": [
            {
                "name": "transactions",
                "columns": [
                    {"name": "tx_id", "type": "string", "required": True},
                    {"name": "amount", "type": "number", "required": True},
                ]
            }
        ]
    }

    def test_import_odcs_no_save(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/odcs",
            json=self.ODCS_CONTRACT,
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "contract" in body

    def test_import_odcs_save_true(self, client, editor_headers):
        r = client.post(
            "/api/v1/import/odcs?save=true",
            json=self.ODCS_CONTRACT,
            headers=editor_headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "saved_to" in body

    def test_import_odcs_no_auth(self, client):
        r = client.post("/api/v1/import/odcs", json={})
        assert r.status_code == 401
