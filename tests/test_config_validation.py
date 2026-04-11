"""
Tests for config.validate_config() — startup env var validation.

Each test patches the relevant config-level variable directly (the module
has already been imported and the int conversions done; we patch the
computed values, not the env vars, to avoid re-import side effects).
"""
import pytest
import opendqv.config as _config


def _run(**overrides):
    """Call validate_config() with specific config attributes temporarily patched."""
    originals = {k: getattr(_config, k) for k in overrides}
    for k, v in overrides.items():
        setattr(_config, k, v)
    try:
        _config.validate_config()
    finally:
        for k, v in originals.items():
            setattr(_config, k, v)


class TestAuthModeValidation:
    def test_open_mode_valid(self):
        _run(AUTH_MODE="open")  # no exception

    def test_token_mode_valid(self):
        _run(AUTH_MODE="token")

    def test_invalid_auth_mode_raises(self):
        with pytest.raises(ValueError, match="AUTH_MODE"):
            _run(AUTH_MODE="basic")

    def test_empty_auth_mode_raises(self):
        with pytest.raises(ValueError, match="AUTH_MODE"):
            _run(AUTH_MODE="")


class TestDbBackendValidation:
    def test_sqlite_valid(self):
        _run(DB_BACKEND="sqlite")

    def test_postgres_with_url_valid(self):
        _run(DB_BACKEND="postgres", DB_URL="postgresql://user:pass@host/db")

    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="OPENDQV_DB_BACKEND"):
            _run(DB_BACKEND="mysql")

    def test_postgres_without_url_raises(self):
        with pytest.raises(ValueError, match="OPENDQV_DB_URL"):
            _run(DB_BACKEND="postgres", DB_URL="")


class TestIntVarValidation:
    def test_token_expiry_zero_raises(self):
        with pytest.raises(ValueError, match="TOKEN_EXPIRY_DAYS"):
            _run(TOKEN_EXPIRY_DAYS=0)

    def test_token_expiry_negative_raises(self):
        with pytest.raises(ValueError, match="TOKEN_EXPIRY_DAYS"):
            _run(TOKEN_EXPIRY_DAYS=-1)

    def test_token_expiry_valid(self):
        _run(TOKEN_EXPIRY_DAYS=30)

    def test_max_batch_rows_zero_raises(self):
        with pytest.raises(ValueError, match="OPENDQV_MAX_BATCH_ROWS"):
            _run(MAX_BATCH_ROWS=0)

    def test_max_batch_rows_valid(self):
        _run(MAX_BATCH_ROWS=10000)

    def test_max_sse_connections_zero_raises(self):
        with pytest.raises(ValueError, match="OPENDQV_MAX_SSE_CONNECTIONS"):
            _run(MAX_SSE_CONNECTIONS=0)

    def test_max_isolation_hours_zero_raises(self):
        with pytest.raises(ValueError, match="OPENDQV_MAX_ISOLATION_HOURS"):
            _run(MAX_ISOLATION_HOURS=0)


class TestRateLimitValidation:
    def test_valid_per_minute(self):
        _run(RATE_LIMIT_DEFAULT="120/minute")

    def test_valid_per_second(self):
        _run(RATE_LIMIT_VALIDATE="10/second")

    def test_valid_per_hour(self):
        _run(RATE_LIMIT_TOKENS="5/hour")

    def test_off_sentinel_valid(self):
        _run(RATE_LIMIT_DEFAULT="off")

    def test_disabled_sentinel_valid(self):
        _run(RATE_LIMIT_VALIDATE="disabled")

    def test_zero_sentinel_valid(self):
        _run(RATE_LIMIT_TOKENS="0")

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="RATE_LIMIT_DEFAULT"):
            _run(RATE_LIMIT_DEFAULT="120/day")

    def test_missing_number_raises(self):
        with pytest.raises(ValueError, match="RATE_LIMIT_VALIDATE"):
            _run(RATE_LIMIT_VALIDATE="minute")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="RATE_LIMIT_TOKENS"):
            _run(RATE_LIMIT_TOKENS="")


class TestValidConfigPassesClean:
    def test_default_config_is_valid(self):
        """The default config (as loaded by tests) should pass validation."""
        _config.validate_config()
