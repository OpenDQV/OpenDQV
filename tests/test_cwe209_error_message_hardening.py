"""CWE-209 — caught exception detail must not be echoed to API clients.

Regression guard for the 2026-08-05 hardening. Five CodeQL py/stack-trace-exposure
findings shared one shape: a caught runtime exception's message was embedded into
a response/result field (`str(exc)` / `f"...: {exc}"`). The fix logs the detail
server-side and returns a generic, bounded message instead.

Two layers of guard:
  1. Source-level — the specific vulnerable expressions must not reappear in the
     five hardened modules (catches a copy-paste regression directly).
  2. Behavioural — the trace-log verifier returns a generic, location-only parse
     error that keeps the stable "JSON parse error" prefix but drops the raw
     JSONDecodeError text.
"""

import inspect

import opendqv.api.routes_federation as _fed
import opendqv.core.trace_log as _trace
import opendqv.core.importers.great_expectations as _gx
import opendqv.core.importers.soda as _soda
import opendqv.core.importers.dbt as _dbt
import opendqv.core.importers.csv_rules as _csv


# Vulnerable fragments assembled from parts so this file does not flag itself
# when the guard greps for them (mirrors the TestNoRegressedTestPatterns idiom).
_EXC = "{" + "exc}"
_E = "{" + "e}"
_HANDLER_LEAK = "handler error: " + _EXC          # f"handler error: {exc}"
_PARSE_LEAK = "JSON parse error: " + _E           # f"JSON parse error: {e}"
_PEER_LEAK = 'peer_error"] = str(' + "exc)"       # result["peer_error"] = str(exc)


class TestNoExceptionTextInResponses:
    def test_importers_use_generic_handler_reason(self):
        for mod in (_gx, _soda, _dbt, _csv):
            src = inspect.getsource(mod)
            assert _HANDLER_LEAK not in src, (
                f"{mod.__name__} reintroduced raw exception text in a skip reason"
            )
            assert '"reason": "handler error"' in src, (
                f"{mod.__name__} lost the generic handler-error reason"
            )

    def test_trace_log_parse_error_is_generic(self):
        src = inspect.getsource(_trace)
        assert _PARSE_LEAK not in src

    def test_federation_peer_error_is_generic(self):
        src = inspect.getsource(_fed)
        assert _PEER_LEAK not in src
        assert 'result["peer_error"] = "peer sync failed"' in src


class TestTraceLogParseErrorBehaviour:
    def test_json_parse_error_omits_exception_text(self, tmp_path):
        bad = tmp_path / "trace.jsonl"
        # Malformed JSON — the raw JSONDecodeError would name line/column/char.
        bad.write_text("{not valid json at all\n", encoding="utf-8")
        result = _trace.verify_trace_log(str(bad))
        assert result["valid"] is False
        # Stable prefix preserved (consumers/tests rely on it) ...
        assert "JSON parse error" in result["error"]
        # ... but the raw parser detail is gone.
        assert "line 1" not in result["error"]
        assert "column" not in result["error"]
        assert "char" not in result["error"]
        # It reports the entry index instead.
        assert "entry" in result["error"]
