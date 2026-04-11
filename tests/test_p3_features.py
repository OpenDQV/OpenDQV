"""Tests for P3 features: built-in patterns, CLI import-dir."""

from opendqv.core.rule_parser import Rule
from opendqv.core.validator import validate_record


class TestBuiltinPatterns:
    """P3 — built-in pattern shorthands."""

    def test_builtin_semver_valid(self):
        rule = Rule(name="r", type="regex", field="version",
                    pattern="builtin:semver", error_message="Invalid semver")
        assert validate_record({"version": "1.2.3"}, [rule])["valid"] is True
        assert validate_record({"version": "2.0.0-beta.1"}, [rule])["valid"] is True

    def test_builtin_semver_invalid(self):
        rule = Rule(name="r", type="regex", field="version",
                    pattern="builtin:semver", error_message="Invalid semver")
        assert validate_record({"version": "not.a.version"}, [rule])["valid"] is False

    def test_builtin_ipv4_valid(self):
        rule = Rule(name="r", type="regex", field="ip",
                    pattern="builtin:ipv4", error_message="Invalid IPv4")
        assert validate_record({"ip": "192.168.1.1"}, [rule])["valid"] is True
        assert validate_record({"ip": "10.0.0.1"}, [rule])["valid"] is True

    def test_builtin_ipv4_invalid(self):
        rule = Rule(name="r", type="regex", field="ip",
                    pattern="builtin:ipv4", error_message="Invalid IPv4")
        assert validate_record({"ip": "999.999.999.999"}, [rule])["valid"] is False
        assert validate_record({"ip": "not-an-ip"}, [rule])["valid"] is False

    def test_builtin_cve_id_valid(self):
        rule = Rule(name="r", type="regex", field="cve",
                    pattern="builtin:cve_id", error_message="Invalid CVE ID")
        assert validate_record({"cve": "CVE-2023-12345"}, [rule])["valid"] is True
        assert validate_record({"cve": "CVE-2021-44228"}, [rule])["valid"] is True

    def test_builtin_cve_id_invalid(self):
        rule = Rule(name="r", type="regex", field="cve",
                    pattern="builtin:cve_id", error_message="Invalid CVE ID")
        assert validate_record({"cve": "cve-2023-12345"}, [rule])["valid"] is False  # lowercase
        assert validate_record({"cve": "CVE-23-1234"}, [rule])["valid"] is False     # short year

    def test_builtin_smpte_timecode_valid(self):
        rule = Rule(name="r", type="regex", field="tc",
                    pattern="builtin:smpte-timecode", error_message="Invalid timecode")
        assert validate_record({"tc": "01:00:00:00"}, [rule])["valid"] is True
        assert validate_record({"tc": "23:59:59;29"}, [rule])["valid"] is True  # drop-frame

    def test_builtin_uuid_valid(self):
        rule = Rule(name="r", type="regex", field="id",
                    pattern="builtin:uuid", error_message="Invalid UUID")
        assert validate_record({"id": "550e8400-e29b-41d4-a716-446655440000"}, [rule])["valid"] is True

    def test_builtin_uuid_invalid(self):
        rule = Rule(name="r", type="regex", field="id",
                    pattern="builtin:uuid", error_message="Invalid UUID")
        assert validate_record({"id": "not-a-uuid"}, [rule])["valid"] is False

    def test_builtin_did_valid(self):
        rule = Rule(name="r", type="regex", field="did",
                    pattern="builtin:did", error_message="Invalid DID")
        assert validate_record({"did": "did:example:123456"}, [rule])["valid"] is True
        assert validate_record({"did": "did:web:example.com"}, [rule])["valid"] is True

    def test_builtin_email_valid(self):
        rule = Rule(name="r", type="regex", field="email",
                    pattern="builtin:email", error_message="Invalid email")
        assert validate_record({"email": "user@example.com"}, [rule])["valid"] is True

    def test_builtin_email_invalid(self):
        rule = Rule(name="r", type="regex", field="email",
                    pattern="builtin:email", error_message="Invalid email")
        assert validate_record({"email": "not-an-email"}, [rule])["valid"] is False

    def test_builtin_negate_semver(self):
        # negate + builtin: field must NOT be a valid semver
        rule = Rule(name="r", type="regex", field="tag",
                    pattern="builtin:semver", negate=True,
                    error_message="Must not be semver format")
        assert validate_record({"tag": "latest"}, [rule])["valid"] is True
        assert validate_record({"tag": "1.0.0"}, [rule])["valid"] is False
