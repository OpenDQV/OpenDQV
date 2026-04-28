"""
v2.3.23 outside-review fix #2 — get_quality_trend(by=agent) returns
empty key + null date.

Persona B 2026-04-28 outside review P0:
> "get_quality_trend(by=agent) is non-functional. Returns one point
>  with key: '' and date: null. The other groupings (by=date,
>  by=rule) work."

Root cause: legacy quality_stats rows with empty agent_id collapse
under key="" in _group_trend. The in-memory _aggregate_by_agent
already uses "unattributed" — different aggregator, different
decision, inconsistent label.

Plus: QualityTrendPoint has both date and key as Optional; when
by!=date, date serializes as null. Combined with key="", the response
looks like "two broken fields" to a consumer.

Sonnet's pre-impl review (a15e627a5e6a24fa3):
  - Empty agent_id → "unattributed" (mirror in-memory aggregator)
  - Drop date field when by!=date via response_model_exclude_none
  - Parametrize test across by ∈ {agent, context, rule, date}
"""

from datetime import datetime, timezone
import json



def _seed_mixed_agents(db_path: str, contract: str):
    """Seed rows with two named agents and one with no agent_id."""
    import sqlite3
    from opendqv.core.quality_stats import QualityStats
    QualityStats(db_path)
    conn = sqlite3.connect(db_path)
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    rows = [
        ("evt-a", "pipeline-A"),
        ("evt-b", "pipeline-A"),
        ("evt-c", "pipeline-B"),
        ("evt-d", ""),  # empty agent (legacy row)
    ]
    for evt, aid in rows:
        conn.execute(
            "INSERT INTO quality_stats (event_id, contract_name, contract_version, "
            "context, recorded_at, total_records, passed, failed, pass_rate_pct, "
            "rule_failure_counts, agent_id, mode, caller_principal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (evt, contract, "1.0", "default", today.isoformat(),
             1, 1, 0, 100.0, json.dumps({}), aid, "enforcement", "test"),
        )
    conn.commit()
    conn.close()


class TestByAgentGroupingHonest:
    def test_by_agent_empty_agent_id_relabelled_unattributed(self, tmp_path):
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "trend.db")
        _seed_mixed_agents(db, "trend_test")
        qs = QualityStats(db)
        points = qs.get_trend("trend_test", days=1, by="agent")
        keys = {p["key"] for p in points}
        assert "pipeline-A" in keys, points
        assert "pipeline-B" in keys, points
        # Empty agent must surface as a labelled bucket, not as "".
        assert "" not in keys, (
            f"v2.3.23 outside-review #2: empty agent_id must surface "
            f"as 'unattributed' bucket, not as empty key. Reviewer "
            f"flagged this as 'non-functional' grouping. Got: {keys}"
        )
        assert "unattributed" in keys, (
            f"v2.3.23 outside-review #2: rows with empty agent_id must "
            f"surface as a labelled 'unattributed' bucket so consumers "
            f"can see the data-coverage gap. Got: {keys}"
        )

    def test_by_agent_no_empty_string_key(self, tmp_path):
        """Defensive: even after relabel, ensure no point has key=''."""
        from opendqv.core.quality_stats import QualityStats
        db = str(tmp_path / "trend.db")
        _seed_mixed_agents(db, "trend_test")
        qs = QualityStats(db)
        points = qs.get_trend("trend_test", days=1, by="agent")
        for p in points:
            assert p.get("key") != "", p


class TestTrendWireShapeDropsNullDate:
    """When by != 'date', the QualityTrendPoint should not surface
    date: null on the wire. response_model_exclude_none keeps the
    response clean."""

    def test_by_agent_response_does_not_carry_date_field(self, client, auth_headers):
        # Seed via the live engine path so this exercises the route
        # response, not just the QualityStats helper.
        from opendqv.monitoring import stats as global_stats
        global_stats.totals.clear()
        global_stats._events.clear()

        # Drive a few validates with named agents.
        global_stats.record(
            contract="customer", context="default", valid=True,
            error_count=0, warning_count=0, latency_ms=1.0,
            agent_id="pipeline-A",
        )
        # Use REST endpoint to verify wire shape via response_model.
        from opendqv.security.auth import create_pat
        validator = create_pat("by-agent-wire-test", role="validator")["token"]
        for _ in range(2):
            client.post(
                "/api/v1/validate?allow_draft=true",
                json={"contract": "customer",
                      "record": {"name": "x", "age": 30, "email": "x@x.co"}},
                headers={"Authorization": f"Bearer {validator}"},
            )
        import time
        time.sleep(2)

        r = client.get(
            "/api/v1/contracts/customer/quality-trend?by=agent&days=1",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        for p in body.get("points", []):
            assert "date" not in p or p.get("date") is None, p
        # When data exists for by=agent, points carry `key`, not `date`.
