"""
ACT-040-06 — Throughput baseline test.

This is NOT a load test.  It validates that the pure-Python validation engine
can process at least 100 records/second sequentially on the development
machine, which is the floor needed to sustain 380M records/month at peak.

The full benchmark methodology and results are documented in
docs/benchmark_throughput.md.
"""

import time
from pathlib import Path
from unittest.mock import patch


from core.contracts import ContractRegistry
from core.validator import validate_record, _load_lookup_set


CONTRACTS_DIR = Path(__file__).parent.parent / "contracts"

# A valid record that passes all universal_benchmark rules (no lookup fields
# so no file I/O is needed — pure rule evaluation speed).
_VALID_RECORD = {
    "record_id": "rec-00001",
    "email": "bench@example.com",
    "amount": 42.50,
    "created_date": "2024-01-15",
    "status": "ACTIVE",
    "currency": "GBP",
    "score": 75,
    "is_active": "true",
    "name": "Benchmark User",
    "country_code": "GB",
    "phone": "+441234567890",
    "postcode": "SW1A 1AA",
    "age": 30,
    "dob": "1994-03-15",
}

_N = 1_000
_TARGET_SECONDS = 10.0  # 1,000 records in under 10 s  →  100 records/s floor


class TestThroughputBaseline:
    """Smoke-level performance test — not a full load test."""

    def test_throughput_baseline(self):
        """
        1,000 sequential validate_record calls against universal_benchmark
        must complete in under 10 seconds.

        This establishes that the validation engine is above the 100 rec/s
        floor.  For comparison, 380 M records/month ≈ 146 rec/s sustained;
        the full gunicorn stack with 4 workers comfortably exceeds this.
        """
        _load_lookup_set.cache_clear()

        with patch("config.CONTRACTS_DIR", CONTRACTS_DIR):
            registry = ContractRegistry(CONTRACTS_DIR)
            contract = registry.get("universal_benchmark")
            assert contract is not None, "universal_benchmark contract not found"
            rules = contract.rules

            start = time.perf_counter()
            for i in range(_N):
                record = dict(_VALID_RECORD)
                record["record_id"] = f"rec-{i:05d}"
                validate_record(record, rules, "universal_benchmark")
            elapsed = time.perf_counter() - start

        _load_lookup_set.cache_clear()

        rate = _N / elapsed
        assert elapsed < _TARGET_SECONDS, (
            f"Throughput too low: {_N} records took {elapsed:.2f}s "
            f"({rate:.0f} rec/s). Expected > {_N / _TARGET_SECONDS:.0f} rec/s."
        )
