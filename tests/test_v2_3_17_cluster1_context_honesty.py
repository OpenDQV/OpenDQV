"""
v2.3.17 Cluster 1 — context honesty, as of 3.0.0.

v2.3.17 locked two behaviours around contract ``contexts:`` overrides:
overrides applied on the historical-hash path (F-A) and a
``context_warning`` on the validate response when the supplied context
was not declared on the contract (F-D).

3.0.0 removes ``contexts:`` entirely. ``context`` on /validate is a pure
tag (quality stats, audit, metrics) that never changes which rules run,
so there is nothing for a warning to report: no ``context_warning`` is
emitted, and a contract that still carries a ``contexts:`` block is
refused at load with ``CONTEXTS_UNSUPPORTED`` rather than silently
honoured or ignored.
"""

from opendqv.core.contracts import CONTEXTS_UNSUPPORTED, ContractRegistry


class TestRestValidateContextIsATag:
    def test_any_context_validates_against_base_rules_without_warning(self, client, auth_headers):
        record = {"name": "Alice", "age": 30, "email": "a@b.co"}
        base = client.post("/api/v1/validate?allow_draft=true",
                           json={"contract": "customer", "record": record}, headers=auth_headers)
        tagged = client.post("/api/v1/validate?allow_draft=true",
                             json={"contract": "customer", "record": record, "context": "prodd"},
                             headers=auth_headers)
        assert base.status_code == tagged.status_code == 200
        assert tagged.json()["valid"] is base.json()["valid"] is True
        assert "context_warning" not in tagged.json()

    def test_no_context_no_warning(self, client, auth_headers):
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        assert "context_warning" not in r.json()


class TestContextsBlockRefusedAtLoad:
    def test_contract_carrying_contexts_does_not_load(self, tmp_path):
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "test_contract.yaml").write_text("""
name: test_contract
status: active
version: "1.0"
rules:
  - name: amount_positive
    field: amount
    type: range
    min: 0
contexts:
  billing:
    amount:
      type: range
      min: 1000
""", encoding="utf-8")
        reg = ContractRegistry(contracts_dir)
        assert reg.get("test_contract") is None
        assert [f["file"] for f in reg.load_failures] == ["test_contract.yaml"]
        assert reg.load_failures[0]["error"] == CONTEXTS_UNSUPPORTED
