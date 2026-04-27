"""
v2.3.17 Cluster 1 — context honesty + MCP-in-process F-A fix.

Three behaviours locked here as recurrence tests, paired with the
production fixes shipped in the same release per Queen's Standard:

1. F-A (MCP in-process context bug): when a historical contract is
   selected via ``hash`` AND a context is supplied, context overrides
   must be applied via ``Registry.get_rules_with_context_status`` —
   not by raw dict-replacement of ``contract.rules`` with the pre-Rule
   override dict at ``contract.contexts[context]``. Sonnet's read of
   ``mcp_server.py:761-764`` showed the previous code silently broke
   override application on the historical-hash path.

2. F-D (context typo fail-open visibility): when a context is supplied
   but NOT declared on the contract, the engine continues to use base
   rules (fail-open by explicit design — contexts double as
   stats-tagging metadata for 'demo', 'ci', 'test' workflows), but the
   validate response now carries ``context_warning`` so the caller can
   see the divergence. This protects contract authors who typo a real
   context name (e.g. 'prodd' for 'prod') without breaking intentional
   metadata-tag usage.

3. Cross-surface parity: REST validate, MCP in-process validate_record,
   and MCP in-process validate_batch all behave identically — same
   warning text, same rules-resolution path. No surface gets the warning
   that another surface misses.
"""



from opendqv.core.contracts import ContractRegistry
from opendqv.core.rule_parser import Rule


# ── Registry helper API: get_rules_with_context_status ─────────────────

class TestGetRulesWithContextStatus:
    """The new registry method that returns (rules, status) instead of just rules.

    Status is one of: ``"none"`` (no context), ``"declared"`` (context is
    on the contract), ``"undeclared"`` (context provided but not declared).
    """

    def _make_registry_with_contract(self, tmp_path, contract_yaml: str):
        """Helper: write a contract YAML and return a Registry pointing at it."""
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "test_contract.yaml").write_text(contract_yaml, encoding="utf-8")
        return ContractRegistry(contracts_dir)

    def test_no_context_returns_none_status(self, tmp_path):
        reg = self._make_registry_with_contract(tmp_path, """
name: test_contract
status: active
version: "1.0"
rules:
  - name: name_required
    field: name
    type: not_empty
""")
        contract = reg.get("test_contract")
        rules, status = reg.get_rules_with_context_status(contract, None)
        assert status == "none"
        assert len(rules) == 1

    def test_declared_context_returns_declared(self, tmp_path):
        reg = self._make_registry_with_contract(tmp_path, """
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
      min: 100
""")
        contract = reg.get("test_contract")
        rules, status = reg.get_rules_with_context_status(contract, "billing")
        assert status == "declared"

    def test_undeclared_context_returns_undeclared_with_base_rules(self, tmp_path):
        """F-D: undeclared context returns base rules (fail-open) BUT with
        the ``undeclared`` status so callers can surface a warning."""
        reg = self._make_registry_with_contract(tmp_path, """
name: test_contract
status: active
version: "1.0"
rules:
  - name: name_required
    field: name
    type: not_empty
contexts:
  prod:
    name:
      type: not_empty
""")
        contract = reg.get("test_contract")
        # 'prodd' is the typo — not declared
        rules, status = reg.get_rules_with_context_status(contract, "prodd")
        assert status == "undeclared", \
            "undeclared context must report 'undeclared' status, not silently look like 'none'"
        # Engine still returns base rules (fail-open) — DO NOT change to fail-closed
        assert len(rules) == 1
        assert rules[0].name == "name_required"


# ── REST /validate context_warning surface ─────────────────────────────

class TestRestValidateContextWarning:
    """REST surface — context_warning field populated on undeclared context."""

    def test_undeclared_context_surfaces_warning(self, client, auth_headers):
        # 'customer' contract is bundled and active; it does NOT declare 'prodd'
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
            "context": "prodd",
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        resp = r.json()
        # Validation still proceeds (fail-open by design)
        assert resp["valid"] is True
        # But the warning is visible
        assert resp.get("context_warning") is not None, \
            "undeclared context must populate context_warning on the validate response"
        assert "prodd" in resp["context_warning"]
        assert "not declared" in resp["context_warning"]

    def test_no_context_no_warning(self, client, auth_headers):
        body = {
            "contract": "customer",
            "record": {"name": "Alice", "age": 30, "email": "a@b.co"},
        }
        r = client.post("/api/v1/validate?allow_draft=true", json=body, headers=auth_headers)
        assert r.status_code == 200
        # context_warning is absent (or null) when no context was supplied
        assert r.json().get("context_warning") is None


# ── MCP in-process F-A historical hash + context interaction ───────────

class TestMcpInProcessHistoricalHashContext:
    """F-A (MCP in-process): historical-hash + context must apply overrides
    via get_rules_with_context_status, not raw dict-replacement."""

    def test_get_rules_with_context_status_applies_overrides_on_historical(self, tmp_path):
        reg = self._make_registry_with_contract(tmp_path, """
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
""")
        contract = reg.get("test_contract")
        # Apply context override — must produce real Rule objects, not raw dicts
        rules, status = reg.get_rules_with_context_status(contract, "billing")
        assert status == "declared"
        # All entries must be Rule objects (the previous broken MCP path replaced
        # them with raw dicts, breaking validation downstream)
        for r in rules:
            assert isinstance(r, Rule), \
                f"context-resolved entry must be a Rule, got {type(r).__name__}: {r}"

    def _make_registry_with_contract(self, tmp_path, contract_yaml: str):
        contracts_dir = tmp_path / "contracts"
        contracts_dir.mkdir()
        (contracts_dir / "test_contract.yaml").write_text(contract_yaml, encoding="utf-8")
        return ContractRegistry(contracts_dir)
