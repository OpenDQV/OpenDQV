"""
ACT-040-06 — Throughput baseline tests.

Five standard workloads measured:
  W1  Single-record validation, simple contract (5 rules), 1,000 sequential calls
  W2  Batch validation, mixed contract (10 rules), 1,000 rows via DuckDB
  W3  Batch validation, mixed contract (10 rules), 10,000 rows via DuckDB
  W4  Batch validation, regex-heavy contract (5 rules), 1,000 rows via DuckDB
  W5  Batch validation, numeric-range contract (5 rules), 1,000 rows via DuckDB

These tests establish OpenDQV's throughput floor. They are NOT comparative benchmarks
against dbt, Great Expectations, or Soda — see docs/benchmark_throughput.md for the
methodology to run those comparisons yourself and submit results.

The full benchmark methodology and results are documented in docs/benchmark_throughput.md.
"""

import time
from pathlib import Path
from unittest.mock import patch

from opendqv.core.contracts import ContractRegistry
from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_batch, validate_record, _load_lookup_set


CONTRACTS_DIR = Path(__file__).parent.parent / "opendqv" / "contracts"

# ── Shared valid record for W1 ────────────────────────────────────────────────
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


# ── W1: Single-record, existing universal_benchmark ──────────────────────────

class TestThroughputBaseline:
    """W1 — Single-record sequential validation (pure Python path)."""

    def test_throughput_baseline(self):
        """
        1,000 sequential validate_record calls against universal_benchmark
        must complete in under 10 seconds (100 rec/s floor).
        """
        _load_lookup_set.cache_clear()

        with patch("opendqv.config.CONTRACTS_DIR", CONTRACTS_DIR):
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
        print(f"\n[W1] Single-record: {_N} records in {elapsed:.2f}s = {rate:.0f} rec/s")
        assert elapsed < _TARGET_SECONDS, (
            f"Throughput too low: {_N} records took {elapsed:.2f}s "
            f"({rate:.0f} rec/s). Expected > {_N / _TARGET_SECONDS:.0f} rec/s."
        )


# ── Inline contracts for batch workloads (no file I/O) ───────────────────────

def _mixed_rules() -> list[Rule]:
    """10 rules: not_empty, regex, min, max, range — no lookup or file deps."""
    return [
        Rule(name="id_required", type="not_empty", field="id", error_message="id required"),
        Rule(name="email_format", type="regex", field="email",
             pattern=r"^[\w.+-]+@[\w-]+\.[\w.]+$", error_message="invalid email"),
        Rule(name="name_required", type="not_empty", field="name", error_message="name required"),
        Rule(name="amount_min", type="min", field="amount",
             min_value=0.0, error_message="amount must be >= 0"),
        Rule(name="amount_max", type="max", field="amount",
             max_value=1_000_000.0, error_message="amount too large"),
        Rule(name="score_range", type="range", field="score",
             min_value=0.0, max_value=100.0, error_message="score must be 0-100"),
        Rule(name="status_regex", type="regex", field="status",
             pattern=r"^(ACTIVE|INACTIVE|PENDING)$", error_message="invalid status"),
        Rule(name="code_length_min", type="min_length", field="code",
             min_length=3, error_message="code too short"),
        Rule(name="code_length_max", type="max_length", field="code",
             max_length=20, error_message="code too long"),
        Rule(name="created_date_format", type="date_format", field="created_date",
             date_format="%Y-%m-%d", error_message="invalid date format"),
    ]


def _regex_rules() -> list[Rule]:
    """5 regex rules — tests regex-heavy workload."""
    return [
        Rule(name="email_fmt", type="regex", field="email",
             pattern=r"^[\w.+-]+@[\w-]+\.[\w.]+$", error_message="invalid email"),
        Rule(name="phone_fmt", type="regex", field="phone",
             pattern=r"^\+?[0-9]{7,15}$", error_message="invalid phone"),
        Rule(name="postcode_fmt", type="regex", field="postcode",
             pattern=r"^[A-Z]{1,2}[0-9][0-9A-Z]?\s?[0-9][A-Z]{2}$",
             error_message="invalid UK postcode"),
        Rule(name="status_fmt", type="regex", field="status",
             pattern=r"^(ACTIVE|INACTIVE|PENDING|SUSPENDED)$",
             error_message="invalid status"),
        Rule(name="ref_fmt", type="regex", field="ref",
             pattern=r"^REF-[0-9]{6}$", error_message="invalid reference format"),
    ]


def _range_rules() -> list[Rule]:
    """5 numeric range rules."""
    return [
        Rule(name="score_range", type="range", field="score",
             min_value=0.0, max_value=100.0, error_message="score out of range"),
        Rule(name="amount_range", type="range", field="amount",
             min_value=0.01, max_value=999_999.99, error_message="amount out of range"),
        Rule(name="age_range", type="range", field="age",
             min_value=0.0, max_value=130.0, error_message="age out of range"),
        Rule(name="quantity_range", type="range", field="quantity",
             min_value=1.0, max_value=10_000.0, error_message="quantity out of range"),
        Rule(name="discount_range", type="range", field="discount",
             min_value=0.0, max_value=100.0, error_message="discount out of range"),
    ]


def _make_mixed_records(n: int) -> list[dict]:
    return [
        {
            "id": f"ID-{i:06d}",
            "email": f"user{i}@example.com",
            "name": f"User {i}",
            "amount": float(i % 10_000),
            "score": float(i % 101),
            "status": ["ACTIVE", "INACTIVE", "PENDING"][i % 3],
            "code": f"CODE{i:04d}",
            "created_date": "2024-06-15",
        }
        for i in range(n)
    ]


def _make_regex_records(n: int) -> list[dict]:
    return [
        {
            "email": f"user{i}@example.com",
            "phone": f"+4412345{i:05d}"[:15],
            "postcode": "SW1A 1AA",
            "status": ["ACTIVE", "INACTIVE", "PENDING", "SUSPENDED"][i % 4],
            "ref": f"REF-{i:06d}",
        }
        for i in range(n)
    ]


def _make_range_records(n: int) -> list[dict]:
    return [
        {
            "score": float(i % 101),
            "amount": float((i % 999_999) + 1) / 100.0,
            "age": float(i % 100),
            "quantity": float((i % 9_999) + 1),
            "discount": float(i % 101),
        }
        for i in range(n)
    ]


# ── W2: Batch 1K, mixed contract ─────────────────────────────────────────────

class TestBatch1KMixed:
    """W2 — Batch validation, 1,000 rows, mixed 10-rule contract (DuckDB path)."""

    def test_batch_1k_mixed(self):
        records = _make_mixed_records(1_000)
        rules = _mixed_rules()

        start = time.perf_counter()
        result = validate_batch(records, rules, "bench_mixed_1k")
        elapsed = time.perf_counter() - start

        rate = 1_000 / elapsed
        print(f"\n[W2] Batch 1K mixed: {elapsed:.3f}s = {rate:.0f} rec/s "
              f"(passed={result['summary']['passed']} failed={result['summary']['failed']})")
        assert elapsed < 30.0, f"Batch 1K took too long: {elapsed:.2f}s"
        assert result["summary"]["total"] == 1_000


# ── W3: Batch 10K, mixed contract ────────────────────────────────────────────

class TestBatch10KMixed:
    """W3 — Batch validation, 10,000 rows, mixed 10-rule contract (DuckDB path)."""

    def test_batch_10k_mixed(self):
        records = _make_mixed_records(10_000)
        rules = _mixed_rules()

        start = time.perf_counter()
        result = validate_batch(records, rules, "bench_mixed_10k")
        elapsed = time.perf_counter() - start

        rate = 10_000 / elapsed
        print(f"\n[W3] Batch 10K mixed: {elapsed:.3f}s = {rate:.0f} rec/s "
              f"(passed={result['summary']['passed']} failed={result['summary']['failed']})")
        assert elapsed < 60.0, f"Batch 10K took too long: {elapsed:.2f}s"
        assert result["summary"]["total"] == 10_000


# ── W4: Batch 1K, regex-heavy ────────────────────────────────────────────────

class TestBatch1KRegexHeavy:
    """W4 — Batch validation, 1,000 rows, 5-regex-rule contract (DuckDB path)."""

    def test_batch_1k_regex_heavy(self):
        records = _make_regex_records(1_000)
        rules = _regex_rules()

        start = time.perf_counter()
        result = validate_batch(records, rules, "bench_regex_1k")
        elapsed = time.perf_counter() - start

        rate = 1_000 / elapsed
        print(f"\n[W4] Batch 1K regex: {elapsed:.3f}s = {rate:.0f} rec/s "
              f"(passed={result['summary']['passed']} failed={result['summary']['failed']})")
        assert elapsed < 30.0, f"Batch 1K regex took too long: {elapsed:.2f}s"
        assert result["summary"]["total"] == 1_000


# ── W5: Batch 1K, numeric range ──────────────────────────────────────────────

class TestBatch1KNumericRange:
    """W5 — Batch validation, 1,000 rows, 5-range-rule contract (DuckDB path)."""

    def test_batch_1k_numeric_range(self):
        records = _make_range_records(1_000)
        rules = _range_rules()

        start = time.perf_counter()
        result = validate_batch(records, rules, "bench_range_1k")
        elapsed = time.perf_counter() - start

        rate = 1_000 / elapsed
        print(f"\n[W5] Batch 1K range: {elapsed:.3f}s = {rate:.0f} rec/s "
              f"(passed={result['summary']['passed']} failed={result['summary']['failed']})")
        assert elapsed < 30.0, f"Batch 1K range took too long: {elapsed:.2f}s"
        assert result["summary"]["total"] == 1_000
