"""Tests for the SSE federation events push endpoint.

All streaming tests use ?limit=1 so the generator emits the 'connected' event
and exits cleanly — avoiding the infinite loop that would hang the test suite.
"""

import json

SSE_URL = "/api/v1/federation/events?limit=1"


def _parse_sse(raw: str) -> list[dict]:
    """Parse SSE text into a list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in raw.splitlines():
        if line.startswith("event:"):
            current["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            try:
                current["data"] = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                current["data"] = line[len("data:"):].strip()
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


class TestSSEAuth:
    """Authentication requirements."""

    def test_no_auth_returns_401(self, client):
        r = client.get("/api/v1/federation/events")
        assert r.status_code == 401

    def test_with_auth_returns_200(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        assert r.status_code == 200


class TestSSEHeaders:
    """Response headers for SSE compliance."""

    def test_content_type_is_event_stream(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        assert "text/event-stream" in r.headers["content-type"]

    def test_cache_control_no_cache(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        assert r.headers.get("cache-control") == "no-cache"

    def test_nginx_buffering_disabled(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        assert r.headers.get("x-accel-buffering") == "no"


class TestSSEConnectedEvent:
    """The first (and with limit=1, only) event is 'connected'."""

    def _events(self, client, auth_headers) -> list[dict]:
        r = client.get(SSE_URL, headers=auth_headers)
        return _parse_sse(r.text)

    def test_first_event_is_connected(self, client, auth_headers):
        events = self._events(client, auth_headers)
        assert len(events) >= 1
        assert events[0]["event"] == "connected"

    def test_connected_has_node_id(self, client, auth_headers):
        import opendqv.config as config
        events = self._events(client, auth_headers)
        assert events[0]["data"]["opendqv_node_id"] == config.OPENDQV_NODE_ID

    def test_connected_has_node_state(self, client, auth_headers):
        events = self._events(client, auth_headers)
        assert events[0]["data"]["opendqv_node_state"] in ("online", "degraded", "isolated")

    def test_connected_has_cursor_lsn(self, client, auth_headers):
        events = self._events(client, auth_headers)
        assert "cursor_lsn" in events[0]["data"]
        assert isinstance(events[0]["data"]["cursor_lsn"], int)


class TestSSEQueryParams:
    """Query parameter handling."""

    def test_poll_interval_clamped_min(self, client, auth_headers):
        r = client.get(
            "/api/v1/federation/events?limit=1&poll_interval=0",
            headers=auth_headers,
        )
        assert r.status_code == 200

    def test_heartbeat_interval_param_accepted(self, client, auth_headers):
        r = client.get(
            "/api/v1/federation/events?limit=1&heartbeat_interval=60",
            headers=auth_headers,
        )
        assert r.status_code == 200


class TestSSEWireFormat:
    """SSE wire format is spec-compliant (RFC 8895)."""

    def test_event_lines_have_correct_prefix(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        for line in r.text.splitlines():
            if line:
                assert (
                    line.startswith("event:")
                    or line.startswith("data:")
                    or line.startswith(":")
                ), f"Unexpected SSE line: {line!r}"

    def test_events_separated_by_blank_line(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        assert "\n\n" in r.text

    def test_data_is_valid_json(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        events = _parse_sse(r.text)
        for event in events:
            assert isinstance(event.get("data"), dict), \
                f"data field is not a dict: {event.get('data')!r}"


class TestSSELimitParam:
    """limit=N causes the stream to emit exactly N events and stop."""

    def test_limit_1_returns_one_event(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        events = _parse_sse(r.text)
        assert len(events) == 1

    def test_limit_1_response_body_ends_cleanly(self, client, auth_headers):
        r = client.get(SSE_URL, headers=auth_headers)
        # Body should end with \n\n (double newline terminating the last event)
        assert r.text.endswith("\n\n")
