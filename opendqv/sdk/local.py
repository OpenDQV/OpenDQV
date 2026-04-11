"""
OpenDQV local (in-process) validator.

For use when running the validation engine directly in Python, without a running
API server. Install the package, point it at a contracts directory, validate records.

Example:
    from opendqv.sdk.local import LocalValidator

    v = LocalValidator(contracts_dir="./my_contracts")
    result = v.validate({"email": "test@example.com"}, contract="customer")
    if not result["valid"]:
        raise ValueError(result["errors"])

    # Batch validation
    result = v.validate_batch(records, contract="customer")
    print(result["summary"])
"""

from pathlib import Path
from typing import Optional

from opendqv.core.contracts import ContractRegistry
from opendqv.core.validator import validate_record, validate_batch


class ContractNotFoundError(Exception):
    """Raised when a named contract does not exist in the loaded registry."""


class LocalValidator:
    """
    In-process validator backed by a local directory of YAML contract files.

    No API server, no Docker — pure Python. Useful for:
    - Unit tests that validate records as part of CI
    - CLI tools and ETL scripts that need validation without a network call
    - Development environments without Docker
    - Embedding OpenDQV validation inside another Python application

    Thread safety: ContractRegistry is read-only after construction.
    For long-running processes, call reload() to pick up contract changes.
    """

    def __init__(self, contracts_dir: Optional[str] = None):
        """
        Args:
            contracts_dir: Path to the directory containing *.yaml contract files.
                           Defaults to the OPENDQV_CONTRACTS_DIR env var, or
                           a 'contracts/' subdirectory relative to the current
                           working directory.
        """
        if contracts_dir is not None:
            path = Path(contracts_dir)
        else:
            import os
            env_dir = os.environ.get("OPENDQV_CONTRACTS_DIR")
            path = Path(env_dir) if env_dir else Path.cwd() / "contracts"

        self.contracts_dir = path
        self._registry = ContractRegistry(path)

    def reload(self):
        """Reload all contracts from disk. Call after editing YAML files."""
        self._registry.reload()

    def list_contracts(self) -> list[dict]:
        """Return metadata for all loaded contracts."""
        return self._registry.list_contracts()

    def validate(
        self,
        record: dict,
        contract: str,
        context: Optional[str] = None,
    ) -> dict:
        """
        Validate a single record against a named contract.

        Args:
            record:   The record to validate, as a plain dict.
            contract: Name of the contract (matches the YAML filename stem).
            context:  Optional context name for context-specific rule overrides.

        Returns:
            dict with keys: valid (bool), errors (list), warnings (list),
            contract (str), version (str).

        Raises:
            ContractNotFoundError: if the named contract is not loaded.
        """
        dc = self._registry.get(contract)
        if dc is None:
            raise ContractNotFoundError(
                f"Contract '{contract}' not found. "
                f"Loaded contracts: {[c['name'] for c in self._registry.list_contracts()]}"
            )
        rules = self._registry.get_rules_with_context(dc, context)
        result = validate_record(record, rules)
        result["contract"] = contract
        result["version"] = dc.version
        return result

    def validate_batch(
        self,
        records: list[dict],
        contract: str,
        context: Optional[str] = None,
    ) -> dict:
        """
        Validate a list of records against a named contract.

        Args:
            records:  List of dicts to validate.
            contract: Name of the contract.
            context:  Optional context name.

        Returns:
            dict with keys: summary (dict), results (list), contract (str).

        Raises:
            ContractNotFoundError: if the named contract is not loaded.
        """
        dc = self._registry.get(contract)
        if dc is None:
            raise ContractNotFoundError(
                f"Contract '{contract}' not found. "
                f"Loaded contracts: {[c['name'] for c in self._registry.list_contracts()]}"
            )
        rules = self._registry.get_rules_with_context(dc, context)
        result = validate_batch(records, rules)
        result["contract"] = contract
        result["version"] = dc.version
        return result
