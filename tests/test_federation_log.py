"""Tests for FederationLog — the OSS foundation of the federation sync machinery."""

import pytest
from opendqv.core.federation import FederationLog, FEDERATION_EVENT_TYPES, FEDERATION_STATUSES


class TestFederationLogSchema:
    """Table creation and basic structure."""

    def test_table_created(self):
        log = FederationLog(db_path=":memory:")
        # If no exception, schema initialised correctly
        events = log.get_since(0)
        assert events == []

    def test_all_event_types_defined(self):
        expected = {"push", "pull", "ack", "commit", "reject", "isolation_start", "isolation_end"}
        assert expected == FEDERATION_EVENT_TYPES

    def test_all_statuses_defined(self):
        assert FEDERATION_STATUSES == {"pending", "ack", "committed", "rejected"}


class TestRecordEvent:
    """record_event() inserts rows and returns lsn."""

    def test_record_returns_lsn(self):
        log = FederationLog(db_path=":memory:")
        lsn = log.record_event("push", "customer", "1.0", "global-node")
        assert isinstance(lsn, int)
        assert lsn >= 1

    def test_lsn_is_monotonically_increasing(self):
        log = FederationLog(db_path=":memory:")
        lsn1 = log.record_event("push", "customer", "1.0", "global-node")
        lsn2 = log.record_event("ack",  "customer", "1.0", "eu-node", target_node="global-node")
        assert lsn2 > lsn1

    def test_payload_stored_and_returned(self):
        log = FederationLog(db_path=":memory:")
        payload = {"entry_hash": "abc123", "rule_count": 5}
        log.record_event("push", "customer", "1.0", "global-node", payload=payload)
        events = log.get_since(0)
        assert events[0]["payload"] == payload

    def test_target_node_optional(self):
        log = FederationLog(db_path=":memory:")
        log.record_event("push", "customer", "1.0", "global-node")  # no target = broadcast
        events = log.get_since(0)
        assert events[0]["target_node"] is None

    def test_target_node_stored(self):
        log = FederationLog(db_path=":memory:")
        log.record_event("ack", "customer", "1.0", "eu-node", target_node="global-node")
        events = log.get_since(0)
        assert events[0]["target_node"] == "global-node"

    def test_invalid_event_type_raises(self):
        log = FederationLog(db_path=":memory:")
        with pytest.raises(ValueError, match="event_type"):
            log.record_event("teleport", "customer", "1.0", "node")

    def test_invalid_status_raises(self):
        log = FederationLog(db_path=":memory:")
        with pytest.raises(ValueError, match="status"):
            log.record_event("push", "customer", "1.0", "node", status="flying")

    def test_default_status_is_pending(self):
        log = FederationLog(db_path=":memory:")
        log.record_event("push", "customer", "1.0", "global-node")
        events = log.get_since(0)
        assert events[0]["status"] == "pending"

    def test_explicit_status_committed(self):
        log = FederationLog(db_path=":memory:")
        log.record_event("commit", "customer", "1.0", "global-node", status="committed")
        events = log.get_since(0)
        assert events[0]["status"] == "committed"


class TestUpdateStatus:
    """update_status() transitions event status."""

    def test_pending_to_ack(self):
        log = FederationLog(db_path=":memory:")
        lsn = log.record_event("push", "customer", "1.0", "global-node")
        assert log.update_status(lsn, "ack") is True
        events = log.get_since(0)
        assert events[0]["status"] == "ack"

    def test_update_nonexistent_lsn_returns_false(self):
        log = FederationLog(db_path=":memory:")
        assert log.update_status(9999, "ack") is False

    def test_invalid_status_raises(self):
        log = FederationLog(db_path=":memory:")
        lsn = log.record_event("push", "customer", "1.0", "node")
        with pytest.raises(ValueError):
            log.update_status(lsn, "bogus")

    def test_full_2pc_lifecycle(self):
        """push → ack → committed models the two-phase commit happy path."""
        log = FederationLog(db_path=":memory:")
        push_lsn = log.record_event("push", "customer", "1.0", "global-node",
                                     target_node="eu-node", status="pending")
        log.update_status(push_lsn, "ack")
        commit_lsn = log.record_event("commit", "customer", "1.0", "global-node",
                                       status="committed")

        events = log.get_since(0)
        assert events[0]["lsn"] == push_lsn
        assert events[0]["status"] == "ack"
        assert events[1]["lsn"] == commit_lsn
        assert events[1]["status"] == "committed"


class TestGetSince:
    """get_since() acts as a replication cursor."""

    def test_get_since_zero_returns_all(self):
        log = FederationLog(db_path=":memory:")
        log.record_event("push", "customer", "1.0", "node")
        log.record_event("push", "orders",   "1.0", "node")
        assert len(log.get_since(0)) == 2

    def test_get_since_filters_by_lsn(self):
        log = FederationLog(db_path=":memory:")
        lsn1 = log.record_event("push", "customer", "1.0", "node")
        log.record_event("push", "customer", "2.0", "node")
        log.record_event("push", "customer", "3.0", "node")
        events = log.get_since(lsn1)
        assert len(events) == 2
        assert all(e["lsn"] > lsn1 for e in events)

    def test_get_since_filtered_by_contract(self):
        log = FederationLog(db_path=":memory:")
        log.record_event("push", "customer", "1.0", "node")
        log.record_event("push", "orders",   "1.0", "node")
        log.record_event("push", "customer", "2.0", "node")
        events = log.get_since(0, contract_name="customer")
        assert len(events) == 2
        assert all(e["contract_name"] == "customer" for e in events)

    def test_get_since_ordered_by_lsn(self):
        log = FederationLog(db_path=":memory:")
        for version in ("1.0", "2.0", "3.0"):
            log.record_event("push", "customer", version, "node")
        events = log.get_since(0)
        lsns = [e["lsn"] for e in events]
        assert lsns == sorted(lsns)

    def test_empty_result_when_no_new_events(self):
        log = FederationLog(db_path=":memory:")
        lsn = log.record_event("push", "customer", "1.0", "node")
        assert log.get_since(lsn) == []


class TestGetPending:
    """get_pending() surfaces events awaiting acknowledgement."""

    def test_returns_only_pending(self):
        log = FederationLog(db_path=":memory:")
        lsn1 = log.record_event("push", "customer", "1.0", "node", status="pending")
        lsn2 = log.record_event("push", "customer", "2.0", "node", status="pending")
        log.update_status(lsn1, "ack")
        pending = log.get_pending()
        assert len(pending) == 1
        assert pending[0]["lsn"] == lsn2

    def test_get_pending_filtered_by_contract(self):
        log = FederationLog(db_path=":memory:")
        log.record_event("push", "customer", "1.0", "node", status="pending")
        log.record_event("push", "orders",   "1.0", "node", status="pending")
        pending = log.get_pending(contract_name="customer")
        assert len(pending) == 1
        assert pending[0]["contract_name"] == "customer"

    def test_isolation_events_recorded(self):
        log = FederationLog(db_path=":memory:")
        log.record_event("isolation_start", "customer", "1.0", "eu-node", status="pending")
        log.record_event("isolation_end",   "customer", "1.0", "eu-node", status="pending")
        events = log.get_since(0)
        types = [e["event_type"] for e in events]
        assert "isolation_start" in types
        assert "isolation_end" in types
