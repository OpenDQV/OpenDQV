"""
v2.3.22 post-release inside-view finding B1 — `same_date` compare_op
silently no-ops in the batch path.

Persona B (Data Platform Engineer) outside review on 2026-04-28
reproduced an apparent "uniqueness rule blocks other rules in batch"
defect. Investigation shows the real bug is narrower and bigger:

  v2.3.20 added `same_date` compare_op to validator.py:555 (single-
  record path) for the trade_date_matches_execution_date T+0
  invariant. The batch path at validator.py:1285 only consults
  `_COMPARE_OPS.get(rule.compare_op)` — a dict containing only
  gt/lt/gte/lte/eq/neq. `same_date` is NOT in that dict, so:

      op_fn = _COMPARE_OPS.get("same_date")  # None
      if op_fn:                                # False — branch skipped
          ...

  The whole rule is silently no-op'd in batch. Single-record
  validate fires it correctly (single_record path has an explicit
  `if rule.compare_op == "same_date":` branch); batch validate
  passes any record regardless of T+0 violation.

Customer impact: any batch path through MiFIR transaction reporting
silently passes T+0 violations. Persona B's exact reproduction:
trade_date=2026-04-25 with execution_timestamp=2026-04-27T... PASSED
in batch. Same record fails correctly in single-record validate.

This is a CRT170-J / dual-path family defect (single vs batch parity)
— same shape as the v2.3.20 P1.2 fix that introduced same_date in
the first place. The unit-test patch shipped with v2.3.20 only
exercised single-record. Batch parity test was never written.

Fix shape: mirror the same_date branch from single-record into
batch, BEFORE the `_COMPARE_OPS.get` fallthrough. Date string slice
[:10] approach matches the single-record implementation.
"""

import pytest


class TestSameDateBatchParity:
    """B1: same_date compare_op must fire in batch path identically
    to single-record path. CRT170-J dual-path discipline."""

    def test_batch_same_date_violation_fires_rule(self):
        """The reviewer's exact repro shape. trade_date and
        execution_timestamp differ by 2 days. Batch validate MUST
        flag the record."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_batch

        rules = [
            Rule(
                name="trade_date_matches_execution_date",
                field="trade_date",
                type="compare",
                compare_to="execution_timestamp",
                compare_op="same_date",
                severity=Severity.ERROR,
                error_message="trade_date must equal execution_timestamp date portion (T+0).",
            ),
        ]
        records = [
            {
                "trade_date": "2026-04-25",
                "execution_timestamp": "2026-04-27T10:00:00Z",
            },
        ]
        result = validate_batch(records, rules, contract_name="test_t0")
        assert result["summary"]["failed"] == 1, (
            f"v2.3.22 post-release B1 regression: same_date compare_op "
            f"silently skipped in batch path. Reviewer's repro: "
            f"trade_date=2026-04-25 vs execution=2026-04-27 PASSED. "
            f"Got: {result['summary']}"
        )
        errors = result["results"][0]["errors"]
        assert any(e["rule"] == "trade_date_matches_execution_date" for e in errors), errors

    def test_batch_same_date_match_passes(self):
        """Same-day batch records MUST pass the rule."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_batch

        rules = [
            Rule(
                name="trade_date_matches_execution_date",
                field="trade_date",
                type="compare",
                compare_to="execution_timestamp",
                compare_op="same_date",
                severity=Severity.ERROR,
                error_message="trade_date must equal execution_timestamp date portion.",
            ),
        ]
        records = [
            {"trade_date": "2026-04-27", "execution_timestamp": "2026-04-27T10:00:00Z"},
            {"trade_date": "2026-04-27", "execution_timestamp": "2026-04-27T23:59:59Z"},
        ]
        result = validate_batch(records, rules)
        assert result["summary"]["failed"] == 0, result

    def test_batch_same_date_with_uniqueness_co_present(self):
        """Reviewer's misframed scenario: when uniqueness fires on
        record A, did it block T+0 on record B? Verify both rules
        fire independently (the actual fix preserves rule
        independence)."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_batch

        rules = [
            Rule(name="unique_txid", field="transaction_id", type="unique",
                 severity=Severity.ERROR, error_message="transaction_id must be unique"),
            Rule(name="trade_date_matches_execution_date",
                 field="trade_date", type="compare",
                 compare_to="execution_timestamp", compare_op="same_date",
                 severity=Severity.ERROR,
                 error_message="T+0 only"),
        ]
        records = [
            {"transaction_id": "TX1", "trade_date": "2026-04-27",
             "execution_timestamp": "2026-04-27T10:00:00Z"},
            {"transaction_id": "TX1",  # duplicate — uniqueness fires
             "trade_date": "2026-04-25",  # T+0 violation
             "execution_timestamp": "2026-04-27T10:00:00Z"},
        ]
        result = validate_batch(records, rules)
        # Both records fail uniqueness (both share TX1).
        # Record 1 ALSO fails T+0 — must surface BOTH rules.
        assert result["summary"]["failed"] == 2
        rec1_rules = {e["rule"] for e in result["results"][1]["errors"]}
        assert "unique_txid" in rec1_rules
        assert "trade_date_matches_execution_date" in rec1_rules, (
            f"v2.3.22 post-release: per-record cross-field rules must "
            f"fire independently of uniqueness. Reviewer's misframed "
            f"finding — actual root cause was same_date no-op. "
            f"Got rules on record 1: {rec1_rules}"
        )

    def test_batch_same_date_handles_naive_strings(self):
        """Edge: trade_date YYYY-MM-DD vs execution_timestamp without
        Z suffix. The [:10] slice approach in the single-record
        impl strips both to YYYY-MM-DD; batch must do the same."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_batch

        rules = [
            Rule(name="t0", field="trade_date", type="compare",
                 compare_to="execution_timestamp", compare_op="same_date",
                 severity=Severity.ERROR, error_message="T+0"),
        ]
        records = [
            {"trade_date": "2026-04-27",
             "execution_timestamp": "2026-04-27T10:00:00"},
        ]
        result = validate_batch(records, rules)
        assert result["summary"]["failed"] == 0, result

    def test_batch_same_date_skips_when_either_side_malformed(self):
        """Single-record impl returns None (rule not applicable) when
        either side fails the YYYY-MM-DD shape check. Batch must
        match — don't flag the record (a separate format rule is
        responsible for shape)."""
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_batch

        rules = [
            Rule(name="t0", field="trade_date", type="compare",
                 compare_to="execution_timestamp", compare_op="same_date",
                 severity=Severity.ERROR, error_message="T+0"),
        ]
        records = [
            {"trade_date": "garbage", "execution_timestamp": "2026-04-27T10:00:00Z"},
            {"trade_date": "2026-04-27", "execution_timestamp": "also-garbage"},
        ]
        result = validate_batch(records, rules)
        # Neither record fails the same_date rule — shape mismatch is
        # the date_format rule's concern, not this rule's.
        assert result["summary"]["failed"] == 0, result


class TestSingleVsBatchSameDateParity:
    """Symmetric-edit guard: single-record and batch must agree on
    the same_date compare_op output for the same input."""

    @pytest.mark.parametrize("trade_date,execution_timestamp,expect_fail", [
        ("2026-04-27", "2026-04-27T10:00:00Z", False),  # same date
        ("2026-04-25", "2026-04-27T10:00:00Z", True),   # 2-day mismatch
        ("2026-04-27", "2026-04-26T23:59:59Z", True),   # 1-day mismatch
        ("2026-04-27", "2026-04-27T00:00:00", False),   # boundary midnight
    ])
    def test_paths_agree(self, trade_date, execution_timestamp, expect_fail):
        from opendqv.core.rule_parser import Rule, Severity
        from opendqv.core.validator import validate_record, validate_batch

        rules = [
            Rule(name="t0", field="trade_date", type="compare",
                 compare_to="execution_timestamp", compare_op="same_date",
                 severity=Severity.ERROR, error_message="T+0"),
        ]
        record = {"trade_date": trade_date, "execution_timestamp": execution_timestamp}
        single = validate_record(record, rules)
        batch = validate_batch([record], rules)

        single_failed = not single["valid"]
        batch_failed = batch["summary"]["failed"] == 1
        assert single_failed == batch_failed == expect_fail, (
            f"Path divergence at trade_date={trade_date!r}, "
            f"execution_timestamp={execution_timestamp!r}: "
            f"single_failed={single_failed}, batch_failed={batch_failed}, "
            f"expected_fail={expect_fail}"
        )
