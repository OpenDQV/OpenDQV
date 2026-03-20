"""Tests for the isolation event log."""

import time

from core.isolation_log import IsolationLog
from core.node_health import NodeHealthStateMachine, NodeState


class TestIsolationLogSchema:
    """Table creation and singleton."""

    def test_table_created(self):
        log = IsolationLog(db_path=":memory:")
        assert log.get_events() == []

    def test_module_singleton_exists(self):
        from core.isolation_log import isolation_log
        assert isolation_log is not None
        assert isinstance(isolation_log, IsolationLog)


class TestOpenEvent:
    """open_event() records an isolation start."""

    def test_open_returns_id(self):
        log = IsolationLog(db_path=":memory:")
        event_id = log.open_event("upstream_unreachable")
        assert isinstance(event_id, int)
        assert event_id >= 1

    def test_open_creates_row(self):
        log = IsolationLog(db_path=":memory:")
        log.open_event("timeout")
        events = log.get_events()
        assert len(events) == 1
        assert events[0]["trigger"] == "timeout"
        assert events[0]["ended_at"] is None
        assert events[0]["duration_seconds"] is None
        assert events[0]["exceeded_threshold"] is False

    def test_open_records_max_allowed(self):
        import config
        log = IsolationLog(db_path=":memory:")
        log.open_event("test")
        events = log.get_events()
        assert events[0]["max_allowed_seconds"] == config.MAX_ISOLATION_HOURS * 3600

    def test_open_records_node_id(self):
        import config
        log = IsolationLog(db_path=":memory:")
        log.open_event("test")
        events = log.get_events()
        assert events[0]["opendqv_node_id"] == config.OPENDQV_NODE_ID

    def test_multiple_opens_create_multiple_rows(self):
        log = IsolationLog(db_path=":memory:")
        log.open_event("first")
        log.open_event("second")
        assert len(log.get_events()) == 2


class TestCloseEvent:
    """close_event() fills in duration and resolution."""

    def test_close_sets_ended_at(self):
        log = IsolationLog(db_path=":memory:")
        event_id = log.open_event("upstream_unreachable")
        result = log.close_event(event_id, "upstream_restored")
        assert result is True
        events = log.get_events()
        assert events[0]["ended_at"] is not None

    def test_close_sets_resolution(self):
        log = IsolationLog(db_path=":memory:")
        event_id = log.open_event("upstream_unreachable")
        log.close_event(event_id, "upstream_restored")
        events = log.get_events()
        assert events[0]["resolution"] == "upstream_restored"

    def test_close_sets_duration(self):
        log = IsolationLog(db_path=":memory:")
        event_id = log.open_event("test")
        time.sleep(0.05)
        log.close_event(event_id, "resolved")
        events = log.get_events()
        assert events[0]["duration_seconds"] is not None
        assert events[0]["duration_seconds"] >= 0.0

    def test_close_nonexistent_returns_false(self):
        log = IsolationLog(db_path=":memory:")
        assert log.close_event(9999, "resolved") is False

    def test_close_already_closed_returns_false(self):
        log = IsolationLog(db_path=":memory:")
        event_id = log.open_event("test")
        log.close_event(event_id, "first_close")
        assert log.close_event(event_id, "second_close") is False

    def test_exceeded_threshold_false_for_short_isolation(self):
        log = IsolationLog(db_path=":memory:")
        event_id = log.open_event("test")
        log.close_event(event_id, "resolved")
        events = log.get_events()
        # Default MAX_ISOLATION_HOURS is 72h — a sub-second isolation never exceeds it
        assert events[0]["exceeded_threshold"] is False

    def test_exceeded_threshold_true_when_over_limit(self):
        """Simulate exceeding threshold by setting max_allowed to 0 seconds."""
        log = IsolationLog(db_path=":memory:")
        event_id = log.open_event("test")
        # Directly set max_allowed_seconds to 0 so any duration exceeds it
        conn = log._mem_conn
        conn.execute(
            "UPDATE isolation_events SET max_allowed_seconds = 0 WHERE id = ?",
            (event_id,),
        )
        conn.commit()
        time.sleep(0.01)
        log.close_event(event_id, "resolved")
        events = log.get_events()
        assert events[0]["exceeded_threshold"] is True


class TestGetOpenEvents:
    """get_open_events() returns only unclosed rows."""

    def test_open_event_appears(self):
        log = IsolationLog(db_path=":memory:")
        log.open_event("test")
        assert len(log.get_open_events()) == 1

    def test_closed_event_absent(self):
        log = IsolationLog(db_path=":memory:")
        event_id = log.open_event("test")
        log.close_event(event_id, "resolved")
        assert len(log.get_open_events()) == 0

    def test_mixed_open_and_closed(self):
        log = IsolationLog(db_path=":memory:")
        log.open_event("open_one")
        e2 = log.open_event("open_two")
        log.close_event(e2, "resolved")
        open_events = log.get_open_events()
        assert len(open_events) == 1
        assert open_events[0]["trigger"] == "open_one"


class TestCloseOpenEvents:
    """close_open_events() batch-closes all open events."""

    def test_closes_all_open(self):
        log = IsolationLog(db_path=":memory:")
        log.open_event("a")
        log.open_event("b")
        log.open_event("c")
        closed = log.close_open_events("upstream_restored")
        assert closed == 3
        assert len(log.get_open_events()) == 0

    def test_returns_zero_when_none_open(self):
        log = IsolationLog(db_path=":memory:")
        assert log.close_open_events("resolved") == 0


class TestObserverIntegration:
    """observe_state_change() integrates correctly with NodeHealthStateMachine."""

    def test_isolation_opens_on_isolated_transition(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        log = IsolationLog(db_path=":memory:")
        sm.add_observer(log.observe_state_change)

        sm.transition(NodeState.ISOLATED, "upstream_unreachable")

        open_events = log.get_open_events()
        assert len(open_events) == 1
        assert open_events[0]["trigger"] == "upstream_unreachable"

    def test_isolation_closes_on_online_transition(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        log = IsolationLog(db_path=":memory:")
        sm.add_observer(log.observe_state_change)

        sm.transition(NodeState.ISOLATED, "gone")
        sm.transition(NodeState.ONLINE, "back")

        assert len(log.get_open_events()) == 0
        events = log.get_events()
        assert events[0]["resolution"] == "back"
        assert events[0]["ended_at"] is not None

    def test_isolation_closes_on_degraded_transition(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        log = IsolationLog(db_path=":memory:")
        sm.add_observer(log.observe_state_change)

        sm.transition(NodeState.ISOLATED, "gone")
        sm.transition(NodeState.DEGRADED, "partial_recovery")

        assert len(log.get_open_events()) == 0

    def test_non_isolation_transitions_do_not_open_events(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        log = IsolationLog(db_path=":memory:")
        sm.add_observer(log.observe_state_change)

        sm.transition(NodeState.DEGRADED, "lag")
        sm.transition(NodeState.ONLINE, "recovered")

        assert len(log.get_events()) == 0

    def test_full_isolation_lifecycle(self):
        sm = NodeHealthStateMachine(db_path=":memory:")
        log = IsolationLog(db_path=":memory:")
        sm.add_observer(log.observe_state_change)

        sm.transition(NodeState.ISOLATED, "upstream_unreachable")
        time.sleep(0.05)
        sm.transition(NodeState.ONLINE, "upstream_restored")

        events = log.get_events()
        assert len(events) == 1
        assert events[0]["duration_seconds"] is not None
        assert events[0]["duration_seconds"] >= 0.0
        assert events[0]["exceeded_threshold"] is False  # sub-second, well within 72h
