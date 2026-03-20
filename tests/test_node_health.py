"""Tests for the node health state machine."""

import pytest
from core.node_health import NodeHealthStateMachine, NodeState


class TestNodeHealthSchema:
    """Table creation and genesis row."""

    def test_genesis_row_written_on_init(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        log = sm.get_log()
        assert len(log) == 1
        assert log[0]["state"] == "online"
        assert log[0]["reason"].startswith("node startup")

    def test_initial_state_is_online(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        assert sm.current_state() == NodeState.ONLINE

    def test_custom_initial_state(self):
        sm = NodeHealthStateMachine(db_path=":memory:", initial_state=NodeState.DEGRADED)
        assert sm.current_state() == NodeState.DEGRADED

    def test_second_init_does_not_add_genesis(self):
        """Re-constructing with same :memory: conn would, but file-based DBs should not duplicate."""
        NodeHealthStateMachine(db_path=":memory:")
        # Genesis is written; constructing a second instance with a fresh :memory: also writes one
        sm2 = NodeHealthStateMachine(db_path=":memory:")
        assert len(sm2.get_log()) == 1  # each :memory: DB is independent

    def test_module_singleton_exists(self):
        from core.node_health import node_health
        assert node_health is not None
        assert isinstance(node_health, NodeHealthStateMachine)


class TestTransitions:
    """transition() records valid moves and rejects invalid ones."""

    def test_online_to_degraded(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        result = sm.transition(NodeState.DEGRADED, "replication lag > 30s")
        assert result is True
        assert sm.current_state() == NodeState.DEGRADED

    def test_online_to_isolated(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        assert sm.transition(NodeState.ISOLATED, "upstream unreachable") is True
        assert sm.current_state() == NodeState.ISOLATED

    def test_degraded_to_online(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.DEGRADED, "lag detected")
        assert sm.transition(NodeState.ONLINE, "caught up") is True
        assert sm.current_state() == NodeState.ONLINE

    def test_degraded_to_isolated(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.DEGRADED, "lag")
        sm.transition(NodeState.ISOLATED, "lost contact")
        assert sm.current_state() == NodeState.ISOLATED

    def test_isolated_to_online(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.ISOLATED, "upstream gone")
        sm.transition(NodeState.ONLINE, "upstream restored")
        assert sm.current_state() == NodeState.ONLINE

    def test_isolated_to_degraded(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.ISOLATED, "upstream gone")
        sm.transition(NodeState.DEGRADED, "partial contact restored")
        assert sm.current_state() == NodeState.DEGRADED

    def test_self_transition_is_noop(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        result = sm.transition(NodeState.ONLINE, "still online")
        assert result is False
        assert len(sm.get_log()) == 1  # only genesis row

    def test_self_transition_degraded_noop(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.DEGRADED, "first")
        result = sm.transition(NodeState.DEGRADED, "still degraded")
        assert result is False
        assert len(sm.get_log()) == 2  # genesis + first degraded

    def test_invalid_transition_raises(self):
        """There are no invalid transitions in this 3-state machine — all cross-moves allowed.
        The only invalid calls are wrong types."""
        sm = NodeHealthStateMachine(db_path=":memory:")
        with pytest.raises((ValueError, AttributeError)):
            sm.transition("online")  # str instead of NodeState

    def test_reason_stored_in_log(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.ISOLATED, "timeout after 30s")
        log = sm.get_log()
        isolated_entry = next(e for e in log if e["state"] == "isolated")
        assert isolated_entry["reason"] == "timeout after 30s"


class TestGetLog:
    """get_log() returns entries newest-first."""

    def test_log_ordered_newest_first(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.DEGRADED, "step 1")
        sm.transition(NodeState.ISOLATED, "step 2")
        log = sm.get_log()
        states = [e["state"] for e in log]
        assert states[0] == "isolated"
        assert states[-1] == "online"  # genesis

    def test_log_limit(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.DEGRADED, "a")
        sm.transition(NodeState.ONLINE, "b")
        log = sm.get_log(limit=2)
        assert len(log) == 2

    def test_log_contains_node_id(self):
        import config
        sm = NodeHealthStateMachine(db_path=":memory:")
        log = sm.get_log()
        assert log[0]["opendqv_node_id"] == config.OPENDQV_NODE_ID


class TestTimeInCurrentState:
    """time_in_current_state() measures duration."""

    def test_returns_float(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        t = sm.time_in_current_state()
        assert isinstance(t, float)
        assert t >= 0.0

    def test_increases_over_time(self):
        import time
        sm = NodeHealthStateMachine(db_path=":memory:")
        t1 = sm.time_in_current_state()
        time.sleep(0.05)
        t2 = sm.time_in_current_state()
        assert t2 > t1

    def test_resets_after_transition(self):
        import time
        sm = NodeHealthStateMachine(db_path=":memory:")
        time.sleep(0.05)
        before = sm.time_in_current_state()
        sm.transition(NodeState.DEGRADED, "reset")
        after = sm.time_in_current_state()
        assert after < before  # clock reset on transition


class TestIsolatedSince:
    """isolated_since() returns timestamp only when isolated."""

    def test_not_isolated_returns_none(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        assert sm.isolated_since() is None

    def test_isolated_returns_timestamp(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.ISOLATED, "test")
        ts = sm.isolated_since()
        assert ts is not None
        assert "T" in ts  # ISO 8601

    def test_recovered_returns_none(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        sm.transition(NodeState.ISOLATED, "gone")
        sm.transition(NodeState.ONLINE, "back")
        assert sm.isolated_since() is None


class TestHealthEndpointIntegration:
    """Health endpoint exposes node_state."""

    def test_health_has_node_state(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "opendqv_node_state" in data
        assert data["opendqv_node_state"] in ("online", "degraded", "isolated")

    def test_health_has_isolated_since(self, client):
        import config
        from unittest.mock import patch
        with patch.object(config, "HEALTH_DETAIL", True):
            r = client.get("/health")
            data = r.json()
            assert "isolated_since" in data
            # In normal operation isolated_since should be None
            assert data["isolated_since"] is None
