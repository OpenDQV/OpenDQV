"""
CRT177 Tier 1 validator recurrence tests (Protocol 32 — pair every fix with its guard).

Two engine-correctness fixes from the Fable 5 discovery pass (2026-08-06):

  #1  NaN / ±inf silently passed every numeric rule (min/max/range) on both
      the single-record and DuckDB batch paths — a false-pass in the engine's
      core job. Now rejected as a non-finite type mismatch, WITHOUT regressing
      a genuinely-absent optional numeric field (missing key / null → pass).

  #2  Unicode-digit input to checksum rules crashed the validator with an
      unhandled ValueError (str.isdigit() accepts '²' but int('²') raises) →
      HTTP 500 DoS on /validate. Now a clean "invalid checksum". Plus a
      fail-closed safety net so no future checker exception can 500 the worker.
"""
import pytest

from opendqv.core.validator import validate_record, validate_batch
from opendqv.core.rule_parser import Rule


# ── #1 NaN / inf ────────────────────────────────────────────────────────────
class TestNonFiniteNumericRejected:
    def _rules(self):
        return [
            Rule(name="min", field="p", type="min", min_value=0),
            Rule(name="max", field="p", type="max", max_value=100),
            Rule(name="rng", field="p", type="range", min_value=0, max_value=100),
        ]

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "nan"])
    def test_single_record_rejects_non_finite(self, bad):
        r = validate_record({"p": bad}, self._rules())
        assert r["valid"] is False
        assert r["errors"], f"{bad!r} should have produced an error"

    def test_nan_message_is_accurate(self):
        r = validate_record({"p": float("nan")}, [Rule(name="min", field="p", type="min", min_value=0)])
        assert "finite" in r["errors"][0]["message"].lower()
        assert r["errors"][0]["error_code"] == "OPENDQV_TYPE_MISMATCH"

    @pytest.mark.parametrize("good", [0, 50, 100, 0.0, "50", "28.50"])
    def test_valid_finite_values_still_pass(self, good):
        r = validate_record({"p": good}, self._rules())
        assert r["valid"] is True, f"{good!r} should pass"

    def test_min_only_rule_rejects_inf(self):
        # Regression: inf < min is False, so a single-sided min rule used to
        # let +inf through. It must now fail as non-finite.
        r = validate_record({"p": float("inf")}, [Rule(name="min", field="p", type="min", min_value=0)])
        assert r["valid"] is False

    def test_batch_rejects_nan_and_inf(self):
        rule = Rule(name="min", field="price", type="min", min_value=0)
        records = [
            {"price": 50},           # 0 pass
            {"price": float("nan")}, # 1 reject
            {"price": -5},           # 2 fail (below min)
            {},                      # 3 pass — missing optional field
            {"price": None},         # 4 pass — null → absent
            {"price": float("inf")}, # 5 reject
        ]
        res = validate_batch(records, [rule])
        by_idx = {r["index"]: r for r in res["results"]}
        assert by_idx[0]["valid"] is True
        assert by_idx[1]["valid"] is False
        assert by_idx[2]["valid"] is False
        assert by_idx[3]["valid"] is True   # no regression: absent optional numeric
        assert by_idx[4]["valid"] is True   # no regression: null → absent
        assert by_idx[5]["valid"] is False

    def test_batch_missing_optional_numeric_not_regressed(self):
        # A whole batch of records omitting an optional numeric field must all
        # pass a min/max rule — the pandas NaN-for-missing must not be read as
        # a non-finite value.
        rule = Rule(name="max", field="score", type="max", max_value=100)
        records = [{"other": "x"} for _ in range(20)]
        res = validate_batch(records, [rule])
        assert res["summary"]["failed"] == 0


# ── #2 checksum unicode-digit crash ─────────────────────────────────────────
class TestChecksumUnicodeDigitNoCrash:
    ALGOS = ["mod10_gs1", "nhs_mod11", "cpf_mod11", "isin_luhn", "iban_mod97", "lei_mod97"]

    @pytest.mark.parametrize("algo", ALGOS)
    @pytest.mark.parametrize("length", [8, 10, 11, 12, 20])
    def test_unicode_digits_do_not_crash(self, algo, length):
        # '²' (U+00B2): str.isdigit() True, int('²') raises. Must be a clean
        # invalid result, never an unhandled exception.
        rule = Rule(name=algo, field="c", type="checksum", checksum_algorithm=algo)
        res = validate_record({"c": "²" * length}, [rule])
        assert res["valid"] is False

    @pytest.mark.parametrize("algo", ALGOS)
    def test_mixed_unicode_and_ascii_digits(self, algo):
        rule = Rule(name=algo, field="c", type="checksum", checksum_algorithm=algo)
        res = validate_record({"c": "12²456789012"}, [rule])
        assert res["valid"] is False

    def test_valid_gtin_still_passes(self):
        # A real valid GTIN-13 must still validate (fix rejects only non-ASCII).
        rule = Rule(name="gtin", field="c", type="checksum", checksum_algorithm="mod10_gs1")
        res = validate_record({"c": "4006381333931"}, [rule])
        assert res["valid"] is True
