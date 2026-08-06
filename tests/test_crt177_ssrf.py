"""
CRT177 Tier 1 SSRF recurrence tests (Protocol 32 — pair every fix with its guard).

The shared SEC-008 SSRF guard (assert_url_public — webhooks + lookup rules)
allowed 0.0.0.0, IPv4-mapped IPv6, IPv6 link-local, NAT64, ISATAP, and CGNAT.
Rewritten to BLOCK every IPv6→IPv4 transition/embedding form WHOLESALE and
allow only plain global unicast (see opendqv/core/webhooks.py). Three prior
"unwrap-and-allow" designs each missed a distinct form (NAT64, IPv4-compatible,
ISATAP) — Sonnet caught all three; the wholesale block ends the class.

The federation sync-status route wiring (finding #3) is guarded in
tests/test_federation_api.py::TestFederationSyncCompare.test_sync_rejects_ssrf_peer.
"""
import pytest

from opendqv.core.webhooks import assert_url_public


class TestSSRFGuardBlocklist:
    MUST_BLOCK = [
        "http://0.0.0.0:8000/",
        "http://0:8000/",
        "http://127.0.0.1:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://100.64.1.1/",                          # CGNAT
        "http://[::ffff:169.254.169.254]/",            # IPv4-mapped IMDS
        "http://[::ffff:127.0.0.1]:8000/",             # IPv4-mapped loopback
        "http://[fe80::1]/",                           # IPv6 link-local
        "http://[::]/",                                # unspecified
        "http://[64:ff9b::a9fe:a9fe]/",                # NAT64 → 169.254.169.254 (Sonnet r1)
        "http://[64:ff9b::7f00:1]/",                   # NAT64 → 127.0.0.1
        "http://[::a9fe:a9fe]/",                       # IPv4-compatible → 169.254.169.254 (Sonnet r2)
        "http://[::7f00:1]/",                          # IPv4-compatible → 127.0.0.1
        "http://[::a00:1]/",                           # IPv4-compatible → 10.0.0.1
        "http://[::5efe:169.254.169.254]/",            # ISATAP → 169.254.169.254 (Sonnet r3)
        "http://[::5efe:127.0.0.1]/",                  # ISATAP → 127.0.0.1
        "http://[::200:5efe:a9fe:a9fe]/",              # ISATAP U/L-bit signature (0200:5efe)
        "http://[2001:db8::5efe:a9fe:a9fe]/",          # ISATAP under a non-Teredo prefix (IID signature catch)
        "http://[2002:a00:1::]/",                      # 6to4 → 10.0.0.1
        "http://[2002:808:808::]/",                    # 6to4 → 8.8.8.8 — transition form blocked wholesale
        "http://[::ffff:8.8.8.8]/",                    # IPv4-mapped public — transition form blocked wholesale
        "http://2130706433/",                          # decimal 127.0.0.1 (via getaddrinfo)
        "http://localhost/",
        "http://[2001:db8::1]/",                       # documentation range (not global)
        "ftp://example.com/",                          # non-http scheme
    ]
    MUST_ALLOW = [
        "https://example.com/webhook",
        "http://8.8.8.8/",
        "http://1.1.1.1/",
        "http://[2606:4700:4700::1111]/",              # Cloudflare public v6 (plain global unicast)
        "http://[2620:fe::fe]/",                       # Quad9 public v6 (plain global unicast)
    ]

    @pytest.mark.parametrize("url", MUST_BLOCK)
    def test_blocked(self, url):
        with pytest.raises(ValueError):
            assert_url_public(url, label="Test URL")

    @pytest.mark.parametrize("url", MUST_ALLOW)
    def test_allowed(self, url):
        assert_url_public(url, label="Test URL")  # must not raise

    def test_userinfo_does_not_smuggle_internal_host(self):
        # urlparse().hostname must resolve to the real target, not be fooled by
        # embedded credentials pointing the visible host elsewhere.
        with pytest.raises(ValueError):
            assert_url_public("http://public.com@169.254.169.254/", label="Test URL")
