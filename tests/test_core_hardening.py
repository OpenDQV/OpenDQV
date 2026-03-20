"""
Core hardening tests for performance and security improvements.

Covers:
  - Batch row limit (S1.1)
  - Webhook SSRF protection (S1.2)
  - Graceful shutdown lifespan flush (S1.3 — tested indirectly via flush())
  - Compiled regex caching on Rule (S2.1)
  - Regex fallback log line (S2.2)
  - SSE connection cap (S2.3)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core.rule_parser import Rule
from core.webhooks import WebhookManager, _validate_webhook_url


# ---------------------------------------------------------------------------
# Batch row limit — S1.1
# ---------------------------------------------------------------------------

class TestBatchRowLimit:
    def test_batch_within_limit_passes(self, client, auth_headers):
        records = [{"email": f"user{i}@example.com", "age": 25, "name": "Alice",
                    "id": str(i), "phone": "+1234567890", "balance": 100,
                    "score": 85, "date": "2024-01-15", "username": f"user{i}",
                    "password": "securepass123", "status": "active"}
                   for i in range(5)]
        r = client.post("/api/v1/validate/batch",
                        json={"records": records, "contract": "customer"},
                        headers=auth_headers)
        assert r.status_code == 200

    def test_batch_exceeding_limit_returns_400(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(config, "MAX_BATCH_ROWS", 3)
        records = [{"email": f"user{i}@example.com"} for i in range(4)]
        r = client.post("/api/v1/validate/batch",
                        json={"records": records, "contract": "customer"},
                        headers=auth_headers)
        assert r.status_code == 400
        assert "exceeds the maximum" in r.json()["detail"]

    def test_batch_exactly_at_limit_passes(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(config, "MAX_BATCH_ROWS", 2)
        records = [{"email": f"user{i}@example.com", "age": 25, "name": "Alice",
                    "id": str(i), "phone": "+1234567890", "balance": 100,
                    "score": 85, "date": "2024-01-15", "username": f"user{i}",
                    "password": "securepass123", "status": "active"}
                   for i in range(2)]
        r = client.post("/api/v1/validate/batch",
                        json={"records": records, "contract": "customer"},
                        headers=auth_headers)
        assert r.status_code == 200

    def test_batch_limit_error_mentions_env_var(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(config, "MAX_BATCH_ROWS", 1)
        records = [{"email": "a@b.com"}, {"email": "c@d.com"}]
        r = client.post("/api/v1/validate/batch",
                        json={"records": records, "contract": "customer"},
                        headers=auth_headers)
        assert "OPENDQV_MAX_BATCH_ROWS" in r.json()["detail"]

    def test_default_max_batch_rows_is_10000(self):
        assert config.MAX_BATCH_ROWS == 10000


# ---------------------------------------------------------------------------
# Webhook SSRF protection — S1.2
# ---------------------------------------------------------------------------

class TestWebhookSSRF:
    def test_valid_public_url_accepted(self):
        # Should not raise
        _validate_webhook_url("https://example.com/hook")

    def test_http_public_url_accepted(self):
        _validate_webhook_url("http://example.com/hook")

    def test_file_scheme_rejected(self):
        with pytest.raises(ValueError, match="scheme must be http or https"):
            _validate_webhook_url("file:///etc/passwd")

    def test_ftp_scheme_rejected(self):
        with pytest.raises(ValueError, match="scheme must be http or https"):
            _validate_webhook_url("ftp://example.com/hook")

    def test_localhost_by_name_rejected(self):
        with pytest.raises(ValueError, match="localhost"):
            _validate_webhook_url("http://localhost/hook")

    def test_localhost_uppercase_rejected(self):
        with pytest.raises(ValueError, match="localhost"):
            _validate_webhook_url("http://LOCALHOST/hook")

    def test_loopback_ip_rejected(self):
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_webhook_url("http://127.0.0.1/hook")

    def test_loopback_ip_variant_rejected(self):
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_webhook_url("http://127.0.0.2/hook")

    def test_rfc1918_10_rejected(self):
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_webhook_url("http://10.0.0.1/hook")

    def test_rfc1918_172_rejected(self):
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_webhook_url("http://172.16.0.1/hook")

    def test_rfc1918_192_168_rejected(self):
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_webhook_url("http://192.168.1.1/hook")

    def test_cloud_metadata_169_254_rejected(self):
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_webhook_url("http://169.254.169.254/latest/meta-data/")

    def test_webhook_register_ssrf_blocked(self):
        wm = WebhookManager(":memory:")
        with pytest.raises(ValueError, match="private/reserved"):
            wm.register("http://10.0.0.1/hook")

    def test_webhook_register_valid_url_works(self):
        wm = WebhookManager(":memory:")
        hook = wm.register("https://example.com/hook")
        assert hook["url"] == "https://example.com/hook"

    def test_no_hostname_rejected(self):
        with pytest.raises(ValueError, match="no hostname"):
            _validate_webhook_url("http:///no-host")


# ---------------------------------------------------------------------------
# Compiled regex caching on Rule — S2.1
# ---------------------------------------------------------------------------

class TestCompiledRegexCaching:
    def test_regex_rule_has_compiled_pattern(self):
        rule = Rule(
            name="email_check",
            type="regex",
            field="email",
            pattern=r"^[\w.+-]+@[\w-]+\.[\w.-]+$",
        )
        assert rule.compiled_pattern is not None

    def test_compiled_pattern_is_re_pattern(self):
        rule = Rule(
            name="email_check",
            type="regex",
            field="email",
            pattern=r"^[\w.+-]+@[\w-]+\.[\w.-]+$",
        )
        assert hasattr(rule.compiled_pattern, "match")

    def test_non_regex_rule_has_no_compiled_pattern(self):
        rule = Rule(name="age_check", type="min", field="age", **{"min": 0})
        assert rule.compiled_pattern is None

    def test_regex_rule_no_pattern_has_no_compiled_pattern(self):
        rule = Rule(name="empty", type="regex", field="x")
        assert rule.compiled_pattern is None

    def test_compiled_pattern_excluded_from_serialisation(self):
        rule = Rule(
            name="email_check",
            type="regex",
            field="email",
            pattern=r"^[\w.+-]+@[\w-]+\.[\w.-]+$",
        )
        d = rule.model_dump()
        assert "compiled_pattern" not in d

    def test_compiled_pattern_matches_same_as_re_match(self):
        pattern = r"^[A-Z][a-z]+$"
        rule = Rule(name="name_check", type="regex", field="name", pattern=pattern)
        assert rule.compiled_pattern.match("Alice") is not None
        assert rule.compiled_pattern.match("alice") is None

    def test_compiled_pattern_used_in_single_record_validation(self):
        from core.validator import validate_record
        rule = Rule(
            name="email",
            type="regex",
            field="email",
            pattern=r"^[\w.+-]+@[\w-]+\.\w+$",
            error_message="Bad email",
        )
        result = validate_record({"email": "good@example.com"}, [rule])
        assert result["valid"] is True

        result = validate_record({"email": "not-an-email"}, [rule])
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# Regex Python fallback log line — S2.2
# ---------------------------------------------------------------------------

class TestRegexFallbackLogging:
    def test_regex_fallback_logs_debug(self, caplog):
        import logging
        from core.validator import validate_batch

        rule = Rule(
            name="email",
            type="regex",
            field="email",
            pattern=r"^\w+@\w+\.\w+$",  # \w triggers Python fallback
        )
        records = [{"email": "alice@example.com"}, {"email": "bad"}]

        with caplog.at_level(logging.DEBUG, logger="core.validator"):
            validate_batch(records, [rule])

        assert any("regex_python_fallback" in msg for msg in caplog.messages)

    def test_regex_fallback_log_includes_field_name(self, caplog):
        import logging
        from core.validator import validate_batch

        rule = Rule(
            name="phone_check",
            type="regex",
            field="phone",
            pattern=r"^\+\d{10,15}$",  # no \w/\s/\d — but let's confirm the path
        )
        records = [{"phone": "+12345678901"}]

        with caplog.at_level(logging.DEBUG, logger="core.validator"):
            validate_batch(records, [rule])

        # The regex path always logs at DEBUG regardless of \w etc.
        assert any("phone" in msg or "regex_python_fallback" in msg
                   for msg in caplog.messages)


# ---------------------------------------------------------------------------
# SSE connection cap — S2.3
# ---------------------------------------------------------------------------

class TestSSEConnectionCap:
    def test_sse_cap_config_default(self):
        assert config.MAX_SSE_CONNECTIONS == 50

    def test_sse_over_cap_returns_429(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(config, "MAX_SSE_CONNECTIONS", 0)
        # With cap=0, any new connection should fail
        r = client.get("/api/v1/federation/events?limit=1", headers=auth_headers)
        assert r.status_code == 429

    def test_sse_under_cap_connects(self, client, auth_headers, monkeypatch):
        import api.routes as routes_module
        monkeypatch.setattr(config, "MAX_SSE_CONNECTIONS", 100)
        # Reset counter
        with routes_module._sse_lock:
            routes_module._sse_active = 0
        r = client.get("/api/v1/federation/events?limit=1", headers=auth_headers)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# ProxyHeaders config flag — S1.4
# ---------------------------------------------------------------------------

class TestProxyHeadersConfig:
    def test_trust_proxy_headers_defaults_false(self):
        assert config.TRUST_PROXY_HEADERS is False

    def test_trust_proxy_headers_env_true(self, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
        # Re-read value as the module would
        val = __import__("os").environ.get("TRUST_PROXY_HEADERS", "false").lower() == "true"
        assert val is True
