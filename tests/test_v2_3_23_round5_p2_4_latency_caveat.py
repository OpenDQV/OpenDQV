"""
v2.3.23 round-5 P2-4 — get_contract_latency emits sample_source +
sampling_caveat fields so consumers can detect under-confident
percentiles.

Persona B 2026-04-28 outside review #5 P2:
> Latency sample_size shows 2 in get_quality_metrics despite hundreds
> of validations — implies sampled instrumentation, but it's not
> labelled. Customer impact: dashboard percentiles may not represent
> the true distribution; you'd want to know that.

Cause: MCP-driven validates are dry-run (CRT165 lock) and bypass
the live monitoring stats. The reviewer's "hundreds" were dry-run;
sample_size=2 reflected the legitimate non-dry-run traffic in the
in-memory event window (capped at 10,000 events across all contracts).

Fix: label the sample explicitly. New fields on the latency dict:
  - sample_source: "in_memory_event_window_cap_10000"
  - sampling_caveat: present when sample_size < 30 (conventional
    minimum for stable p95/p99) — explains the under-confidence and
    its causes (dry-run, eviction, low traffic).

Default `sample_size` field unchanged so existing consumers don't
break.
"""

import time



class TestSampleSourceLabel:
    """Every latency response (with or without samples) carries
    sample_source. This is the persistent honesty signal."""

    def test_empty_window_carries_sample_source(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        out = s.get_contract_latency("nonexistent_contract", window_hours=24)
        assert out["sample_size"] == 0
        assert out["sample_source"] == "in_memory_event_window_cap_10000", (
            f"v2.3.23 round-5 P2-4: empty-window response must carry "
            f"sample_source. Got: {out}"
        )
        # Empty window also gets a caveat since 0 < 30.
        assert "sampling_caveat" in out

    def test_populated_window_carries_sample_source(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        now = time.time()
        for i in range(5):
            s._events.append((now - 1, "customer", "default", True, 1.5, ""))
        out = s.get_contract_latency("customer", window_hours=24)
        assert out["sample_size"] == 5
        assert out["sample_source"] == "in_memory_event_window_cap_10000"


class TestSamplingCaveatBelowThreshold:
    """Below the 30-sample threshold the response carries an explicit
    caveat naming the causes (dry-run, eviction, low traffic)."""

    def test_caveat_present_below_threshold(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        now = time.time()
        # 5 events — well below the 30 threshold.
        for _ in range(5):
            s._events.append((now - 1, "customer", "default", True, 1.5, ""))
        out = s.get_contract_latency("customer", window_hours=24)
        assert "sampling_caveat" in out, (
            f"v2.3.23 round-5 P2-4: sample_size below threshold must "
            f"carry sampling_caveat. Got: {out}"
        )
        caveat = out["sampling_caveat"].lower()
        # Must explain the dry-run + eviction causes the reviewer asked about.
        assert "dry-run" in caveat or "dry run" in caveat, caveat
        assert "evict" in caveat or "deque" in caveat, caveat

    def test_caveat_absent_above_threshold(self):
        """30+ samples → no caveat (consumer can trust the percentiles)."""
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        now = time.time()
        for _ in range(35):
            s._events.append((now - 1, "customer", "default", True, 1.5, ""))
        out = s.get_contract_latency("customer", window_hours=24)
        assert out["sample_size"] == 35
        assert "sampling_caveat" not in out, (
            f"v2.3.23 round-5 P2-4: sample_size >= threshold (30) must "
            f"NOT emit sampling_caveat (consumer can trust percentiles). "
            f"Got: {out}"
        )


class TestThresholdConstant:
    """Pin the threshold at 30 so a future reduction is intentional,
    not accidental."""

    def test_threshold_is_30(self):
        from opendqv.monitoring import _LATENCY_SAMPLE_LOW_THRESHOLD
        assert _LATENCY_SAMPLE_LOW_THRESHOLD == 30, (
            f"v2.3.23 round-5 P2-4: latency sampling threshold is 30 "
            f"(conventional minimum for stable p95/p99). Got: "
            f"{_LATENCY_SAMPLE_LOW_THRESHOLD}"
        )


class TestExistingFieldsUnchanged:
    """Backward compat: existing fields keep their semantics."""

    def test_avg_p50_p95_p99_unchanged(self):
        from opendqv.monitoring import ValidationStats
        s = ValidationStats()
        now = time.time()
        for _ in range(50):
            s._events.append((now - 1, "customer", "default", True, 2.0, ""))
        out = s.get_contract_latency("customer", window_hours=24)
        assert out["avg_ms"] == 2.0
        assert out["p50_ms"] == 2.0
        assert out["p95_ms"] == 2.0
        assert out["p99_ms"] == 2.0
        assert out["max_ms"] == 2.0
        assert out["sample_size"] == 50
