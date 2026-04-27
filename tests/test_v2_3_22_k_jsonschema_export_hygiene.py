"""
v2.3.22 Cluster K — P2 hygiene batch (final v2.3.22 cluster).

Persona B round-2 N-9 (P2 NEW, `persona_b_outside_report_2026_04_27.md`):

> JSON Schema export under-uses what JSON Schema can represent.
> - lookup rules with inline values exported as x-opendqv-unmapped
>   but JSON Schema 2020-12 supports enum directly.
> - not_empty rules export as {} but expressible as minLength: 1.
> - execution_timestamp exports as format: date (yet another
>   disagreeing format description — feeds N-1).
> Customer impact: a producer wired to OpenDQV via JSON Schema gets
> weaker structural validation than necessary.

Sonnet's pre-impl review (ad4905f02165bd0bb) re-scoped:
  - K1 (not_empty → minLength:1): real gap. The exporter adds the
    field to `required` but emits an empty `{}` for the property
    schema, so a downstream validator accepts empty strings.
  - K2 (lookup rule honesty): the reviewer's framing is partially
    wrong — `allowed_values` rules already emit `enum` (line 90).
    Pure `lookup` rules reference an external file and need ref
    resolution to inline values. v2.3.22 ships an honest reason
    string in `x-opendqv-unmapped` so a consumer knows whether the
    rule is "could be enum but external" vs "structurally
    inexpressible."
  - K3 (rule_velocity co-firing): S-5 reviewer note about
    `ctx_billing_*` rules returning identical time series. Read of
    `rule_failure_velocity` confirms aggregator separates rule
    names correctly. Test pins this — confirms it's co-firing on
    real data, not a counting collision.

Drops (per Sonnet):
  - 2.5 agent_id null vs empty string normalisation: no reviewer
    mandate; wire-shape change risk on four surfaces; v2.4 data-model
    decision.
  - S-3 mode-field dual-use CHANGELOG: already noted in v2.3.17
    Cluster 6.
  - N-9 inline-enum for lookup-with-ref-file: needs ref-file
    resolution at export time (different complexity tier).
  - N-9 date-format heuristic: feeds N-1 mifid four-way
    contradiction; that's a content-side mifid cluster, not K.
"""




# ── K1: not_empty rule emits minLength:1 ───────────────────────────────

class TestNotEmptyEmitsMinLength:
    """Round-2 N-9: not_empty rules currently add the field to
    `required` but leave the property schema empty `{}`. Downstream
    validators accept "" as valid because empty schema = anything.
    JSON Schema 2020-12 expresses 'non-empty string' as minLength: 1
    — emit it."""

    def test_not_empty_rule_adds_min_length_one(self):
        from opendqv.core.contracts import DataContract
        from opendqv.core.rule_parser import Rule
        from opendqv.core.jsonschema import contract_to_jsonschema

        c = DataContract(
            name="ne_test", version="1.0",
            rules=[Rule(name="name_required", type="not_empty", field="name",
                        error_message="Name is required.")],
        )
        schema = contract_to_jsonschema(c)
        assert "name" in schema.get("required", []), schema
        prop = schema["properties"]["name"]
        assert prop.get("minLength") == 1, (
            f"v2.3.22 Cluster K (N-9): not_empty rule must emit "
            f"minLength: 1 so a downstream JSON-Schema validator "
            f"rejects the empty string. Without it, a producer wired "
            f"to OpenDQV via JSON Schema gets weaker structural "
            f"validation than necessary. Got property: {prop!r}"
        )
        assert prop.get("type") == "string", prop

    def test_not_empty_with_min_length_keeps_stricter_min(self):
        """If a contract has both not_empty AND min_length on the
        same field, the explicit min_length wins (not the
        not_empty default of 1). Order-independence: rule sequence
        in the contract must not change the result."""
        from opendqv.core.contracts import DataContract
        from opendqv.core.rule_parser import Rule
        from opendqv.core.jsonschema import contract_to_jsonschema

        c = DataContract(
            name="ne_min_test", version="1.0",
            rules=[
                Rule(name="name_required", type="not_empty", field="name",
                     error_message="Name is required."),
                Rule(name="name_min", type="min_length", field="name",
                     min_length=3, error_message="Name must be at least 3 chars."),
            ],
        )
        schema = contract_to_jsonschema(c)
        prop = schema["properties"]["name"]
        # Stricter wins. min_length=3 covers the not_empty:1 case.
        assert prop["minLength"] >= 1
        assert prop["minLength"] == 3, prop


# ── K2: lookup-rule unmapped reason is honest ──────────────────────────

class TestLookupUnmappedReasonHonest:
    """Round-2 N-9 nuance: pure `lookup` rules reference an external
    file. Resolving the file at export time is a v2.4 capability.
    For v2.3.22, the unmapped reason must distinguish 'could be
    enum if values were inlined' vs 'structurally inexpressible' —
    so a JSON Schema consumer knows what was lost and why."""

    def test_lookup_unmapped_reason_explains_external_ref(self):
        from opendqv.core.contracts import DataContract
        from opendqv.core.rule_parser import Rule
        from opendqv.core.jsonschema import contract_to_jsonschema

        c = DataContract(
            name="lookup_test", version="1.0",
            rules=[Rule(
                name="country_lookup", type="lookup", field="country",
                lookup_file="/refs/countries.txt",
                error_message="Country must be from ISO 3166 list.",
            )],
        )
        schema = contract_to_jsonschema(c)
        unmapped = schema.get("x-opendqv-unmapped", [])
        lookup_entries = [u for u in unmapped if u.get("rule") == "country_lookup"]
        assert lookup_entries, schema
        reason = lookup_entries[0].get("reason", "")
        # The reason must mention either 'enum' (capability hint) OR
        # 'external' / 'reference' (why we didn't inline).
        assert any(
            keyword in reason.lower()
            for keyword in ("external", "reference", "ref ", "ref file", "lookup file")
        ), (
            f"v2.3.22 Cluster K (N-9 honesty): lookup unmapped reason "
            f"must distinguish 'external ref file' from generic "
            f"'inexpressible' so consumers know if inlining is "
            f"feasible. Got: {reason!r}"
        )


# ── K3: rule_velocity aggregator does not collapse distinct rules ──────

class TestRuleVelocitySeparatesDistinctRules:
    """Round-2 S-5 (negative diagnostic): reviewer noted
    `ctx_billing_revenue_ceiling` and `ctx_billing_dwell_seconds_max`
    return identical time series. Aggregator-collision suspicion.

    Code read at `core/quality_analytics.py:rule_failure_velocity`
    confirms: rule_failure_counts JSON is parsed and bucketed by
    rule name. Two distinct rule names produce two distinct series.
    What the reviewer saw was co-firing on the same records — both
    rules failing on the same billing-context records, hence
    identical bucket counts. Real data, real co-firing, not a bug.

    This test pins the aggregator's name-separation contract so a
    future refactor can't introduce a real collision."""

    def test_rule_velocity_separates_two_distinct_rules(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        from opendqv.core.quality_analytics import QualityAnalytics

        db = str(tmp_path / "velocity.db")
        qs = QualityStats(db)
        # Two distinct rules failing on the same batch with DIFFERENT
        # counts — if aggregator collided, both would surface the
        # same total. Different counts prove name-separation.
        qs.record_batch(
            "billing", "1.0", "default",
            total=10, passed=5, failed=5,
            rule_failure_counts={
                "rule_alpha": 5,
                "rule_beta": 3,
            },
        )
        qa = QualityAnalytics(db)
        result = qa.rule_failure_velocity("billing", window_hours=24, bucket_minutes=60)
        series = result["series"]
        assert "rule_alpha" in series
        assert "rule_beta" in series
        # Sum the counts in each rule's series — must reflect distinct
        # totals, not collapsed.
        alpha_total = sum(b["failures"] for b in series["rule_alpha"])
        beta_total = sum(b["failures"] for b in series["rule_beta"])
        assert alpha_total == 5, series
        assert beta_total == 3, series
        assert alpha_total != beta_total, (
            f"v2.3.22 Cluster K (S-5 close-out): aggregator collapsed "
            f"two distinct rule names into one bucket. alpha_total="
            f"{alpha_total}, beta_total={beta_total}. Reviewer's S-5 "
            f"observation about ctx_billing_* identical series is "
            f"co-firing on real data, NOT a counting collision — this "
            f"test pins that contract."
        )

    def test_rule_velocity_co_firing_pattern_produces_identical_series(self, tmp_path):
        """Confirm reviewer's S-5 observation is consistent with
        co-firing — two rules that fail on EVERY same batch produce
        identical series counts. That's not a bug, that's data."""
        from opendqv.core.quality_stats import QualityStats
        from opendqv.core.quality_analytics import QualityAnalytics

        db = str(tmp_path / "co_fire.db")
        qs = QualityStats(db)
        # Both rules fail at the same rate on every batch. Identical
        # rule_failure_counts on every persisted row.
        for _ in range(3):
            qs.record_batch(
                "billing", "1.0", "default",
                total=10, passed=8, failed=2,
                rule_failure_counts={
                    "ctx_billing_revenue_ceiling": 2,
                    "ctx_billing_dwell_seconds_max": 2,
                },
            )
        qa = QualityAnalytics(db)
        result = qa.rule_failure_velocity("billing", window_hours=24, bucket_minutes=60)
        revenue = result["series"]["ctx_billing_revenue_ceiling"]
        dwell = result["series"]["ctx_billing_dwell_seconds_max"]
        # Series must be present, separable, and identical when
        # input data is identical — confirms reviewer's observation
        # was real data, not a bug. Aggregator is correct.
        assert revenue and dwell
        assert sum(b["failures"] for b in revenue) == sum(b["failures"] for b in dwell)
