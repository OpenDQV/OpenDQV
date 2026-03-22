"""
Rule model field behaviour tests — ACT-RMF series.

Explicitly verifies that every Rule model field which claims to affect
validation behaviour actually does so. Three gaps were found where fields
were defined but had no test explicitly exercising their effect.

Gaps covered here:
- format      (date_format rule) — used in validator but previously only spot-checked
- cache_ttl   (HTTP lookup rule) — passed as parameter but cache TTL effect untested
- lookup_auth_header (HTTP lookup) — passed to HTTP call but header presence untested
"""
from unittest.mock import patch
from core.rule_parser import Rule
from core.validator import validate_record


# ── ACT-RMF-001: date_format — rule.format is used first ──────────────────────

class TestDateFormatField:
    """Explicitly verify that rule.format is tried before hardcoded fallbacks.

    The format field existed on the model but the validator previously ignored
    it and tried only 4 hardcoded formats. This test locks in the correct behaviour.
    """

    def test_custom_format_accepts_matching_value(self):
        """A value matching rule.format must pass."""
        rule = Rule(
            name="ts", type="date_format", field="ts",
            format="%Y-%m-%d %H:%M:%S",
            severity="error", error_message="bad date",
        )
        result = validate_record({"ts": "2026-03-21 09:00:00"}, [rule])
        assert result["valid"] is True, (
            "date_format with format='%Y-%m-%d %H:%M:%S' should accept space-separated datetime"
        )

    def test_non_date_value_fails_regardless_of_format(self):
        """A value that matches no format — custom or fallback — must fail."""
        rule = Rule(
            name="ts", type="date_format", field="ts",
            format="%Y-%m-%d %H:%M:%S",
            severity="error", error_message="bad date",
        )
        result = validate_record({"ts": "not-a-date-at-all"}, [rule])
        assert result["valid"] is False, (
            "date_format should reject a value that matches no format"
        )

    def test_custom_sql_timestamp_format(self):
        """SQL Server-style timestamps (the Dean use case) validate with explicit format."""
        rule = Rule(
            name="created_at", type="date_format", field="created_at",
            format="%Y-%m-%d %H:%M:%S",
            severity="error", error_message="invalid timestamp",
        )
        # This was the exact format that previously failed
        result = validate_record({"created_at": "2026-03-21 09:00:00"}, [rule])
        assert result["valid"] is True

    def test_no_format_field_falls_back_to_iso(self):
        """Without rule.format, standard ISO dates still validate."""
        rule = Rule(
            name="d", type="date_format", field="d",
            severity="error", error_message="bad date",
        )
        result = validate_record({"d": "2026-03-21"}, [rule])
        assert result["valid"] is True

    def test_no_format_field_falls_back_to_iso_timestamp(self):
        """Without rule.format, ISO 8601 timestamps still validate via fallback."""
        rule = Rule(
            name="d", type="date_format", field="d",
            severity="error", error_message="bad date",
        )
        result = validate_record({"d": "2026-03-21T09:00:00"}, [rule])
        assert result["valid"] is True


# ── ACT-RMF-002: cache_ttl — value is passed to the HTTP lookup mechanism ─────

class TestCacheTtlField:
    """Verify that rule.cache_ttl is read and passed to the HTTP lookup layer.

    The validator passes cache_ttl as the third positional argument to
    _load_http_lookup_set. We mock that function to verify the field value
    is correctly plumbed through — not just accepted by the model.
    """

    def test_cache_ttl_is_passed_to_http_lookup(self):
        """The cache_ttl value from the rule is passed as ttl to _load_http_lookup_set."""
        rule = Rule(
            name="r", type="lookup", field="panel_type",
            lookup_file="https://example.com/panels",
            cache_ttl=300,
            severity="error", error_message="invalid panel",
        )
        with patch("core.validator._load_http_lookup_set") as mock_load:
            mock_load.return_value = frozenset(["DIGITAL", "STATIC"])
            result = validate_record({"panel_type": "DIGITAL"}, [rule])

        assert result["valid"] is True
        mock_load.assert_called_once()
        # ttl is the 3rd positional argument: (url, lookup_field, ttl, ...)
        ttl_passed = mock_load.call_args.args[2]
        assert ttl_passed == 300, (
            f"cache_ttl=300 on rule should be passed as ttl=300 to _load_http_lookup_set, "
            f"got {ttl_passed}"
        )

    def test_default_ttl_used_when_cache_ttl_not_set(self):
        """When cache_ttl is not set, the default TTL constant is used."""
        from core.validator import _HTTP_LOOKUP_DEFAULT_TTL
        rule = Rule(
            name="r", type="lookup", field="code",
            lookup_file="https://example.com/codes",
            severity="error", error_message="invalid",
        )
        with patch("core.validator._load_http_lookup_set") as mock_load:
            mock_load.return_value = frozenset(["A"])
            validate_record({"code": "A"}, [rule])

        ttl_passed = mock_load.call_args.args[2]
        assert ttl_passed == _HTTP_LOOKUP_DEFAULT_TTL, (
            f"Without cache_ttl, default TTL ({_HTTP_LOOKUP_DEFAULT_TTL}s) should be used, "
            f"got {ttl_passed}"
        )


# ── ACT-RMF-003: lookup_auth_header — value is passed to the HTTP lookup layer

class TestLookupAuthHeaderField:
    """Verify that rule.lookup_auth_header is read and passed to _load_http_lookup_set.

    The function then places it in the Authorization header when making the HTTP
    request. This test verifies the field is correctly plumbed through the
    validator call chain — not just accepted by the model.
    """

    def test_lookup_auth_header_passed_to_http_lookup(self):
        """lookup_auth_header value is passed as auth_header kwarg to _load_http_lookup_set."""
        rule = Rule(
            name="r", type="lookup", field="entity_id",
            lookup_file="https://internal.example.com/sanctions",
            lookup_auth_header="Bearer test-api-key-12345",
            cache_ttl=60,
            severity="error", error_message="not in approved list",
        )
        with patch("core.validator._load_http_lookup_set") as mock_load:
            mock_load.return_value = frozenset(["ENT-001", "ENT-002"])
            result = validate_record({"entity_id": "ENT-001"}, [rule])

        assert result["valid"] is True
        mock_load.assert_called_once()
        auth_passed = mock_load.call_args.kwargs.get("auth_header")
        assert auth_passed == "Bearer test-api-key-12345", (
            f"lookup_auth_header should be passed as auth_header kwarg, got: {auth_passed}"
        )

    def test_no_auth_header_when_not_set(self):
        """Without lookup_auth_header, auth_header=None is passed."""
        rule = Rule(
            name="r", type="lookup", field="code",
            lookup_file="https://public.example.com/codes",
            cache_ttl=60,
            severity="error", error_message="invalid",
        )
        with patch("core.validator._load_http_lookup_set") as mock_load:
            mock_load.return_value = frozenset(["X"])
            validate_record({"code": "X"}, [rule])

        auth_passed = mock_load.call_args.kwargs.get("auth_header")
        assert auth_passed is None, (
            f"Without lookup_auth_header, auth_header should be None, got: {auth_passed}"
        )
