"""
Webhook notification system for OpenDQV.

Sends HTTP POST notifications when validation events occur (failures, warnings, batch failures).
Persisted in SQLite so webhooks survive server restarts.
"""

import asyncio
import ipaddress
import json
import logging
import socket
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Supported event types
VALID_EVENTS = {
    "opendqv.validation.failed",
    "opendqv.validation.warning",
    "opendqv.batch.failed",
    "opendqv.contract.submitted",   # DRAFT → REVIEW
    "opendqv.contract.approved",    # REVIEW → ACTIVE
    "opendqv.contract.rejected",    # REVIEW → DRAFT
}

# Canonical webhook event payload schemas.
# routes.py MUST include all fields marked required=True when calling notify().
# External consumers (OpenMetadata, SaaS control plane) rely on this structure.
VALIDATION_EVENT_SCHEMA = {
    # Auto-added by notify()
    "event":            {"type": "str",      "required": True,  "example": "opendqv.validation.failed"},
    "timestamp":        {"type": "iso8601",  "required": True,  "example": "2026-03-07T09:00:00Z"},
    # Caller-supplied
    "contract":         {"type": "str",      "required": True,  "example": "customer_record"},
    "contract_version": {"type": "str",      "required": True,  "example": "1.2"},
    "opendqv_node_id":  {"type": "str",      "required": True,  "example": "eu-west-prod"},
    "context":          {"type": "str|None", "required": False, "example": "retail_kyc"},
    "record_id":        {"type": "str|None", "required": False, "example": "rec_abc123"},
    "valid":            {"type": "bool",     "required": True,  "example": False},
    "error_count":      {"type": "int",      "required": True,  "example": 2},
    "warning_count":    {"type": "int",      "required": True,  "example": 0},
    "violations": {
        "type": "list",
        "required": True,
        "items": {
            "field":    {"type": "str", "example": "date_of_birth"},
            "rule":     {"type": "str", "example": "dob_plausible_year"},
            "message":  {"type": "str", "example": "Date of birth implies impossible age"},
            "severity": {"type": "str", "example": "error"},
        },
    },
}

BATCH_EVENT_SCHEMA = {
    "event":            {"type": "str",      "required": True,  "example": "opendqv.batch.failed"},
    "timestamp":        {"type": "iso8601",  "required": True},
    "contract":         {"type": "str",      "required": True},
    "contract_version": {"type": "str",      "required": True},
    "opendqv_node_id":  {"type": "str",      "required": True},
    "context":          {"type": "str|None", "required": False},
    "total":            {"type": "int",      "required": True},
    "passed":           {"type": "int",      "required": True},
    "failed":           {"type": "int",      "required": True},
}


# RFC 6052 NAT64 Well-Known Prefix. is_global is True for the whole /96
# regardless of the embedded IPv4, so where a NAT64 gateway is on-path
# (IPv6-only cloud subnets, e.g. IPv6-only EKS/GKE nodes) a literal such as
# 64:ff9b::a9fe:a9fe would reach 169.254.169.254 (cloud metadata). Unwrap and
# classify the embedded IPv4 instead. Residual: operator-chosen Network-
# Specific Prefixes (RFC 6052 §2.2) are indistinguishable from ordinary global
# IPv6 and cannot be closed by a generic classifier — accepted risk, same tier
# as Teredo and the DNS-rebinding TOCTOU. (CRT177 pre-impl red-team, Sonnet.)
# IPv6 → IPv4 transition / embedding forms. Each one carries or tunnels an
# IPv4 that `ipaddress.is_global` does NOT see (it classifies the v6 wrapper,
# not the embedded v4), so each is an SSRF bypass if allowed. We do NOT try to
# unwrap-and-selectively-allow (that design missed NAT64, then IPv4-compatible,
# then ISATAP across three review rounds — every miss was a live bypass).
# Instead we BLOCK the whole transition space outright: none of these literal
# forms has a legitimate webhook/lookup-egress use, so blocking them is
# fail-safe (a detected form is blocked even if we could not correctly extract
# its embedded v4). Detectable set, by prefix:
#   ::ffff:0:0/96  IPv4-mapped          (RFC 4291 §2.5.5.2)
#   ::/96          IPv4-compatible       (RFC 4291 §2.5.5.1, deprecated)
#   64:ff9b::/96   NAT64 well-known      (RFC 6052)
#   2002::/16      6to4                  (RFC 3056)
#   2001::/32      Teredo                (RFC 4380)
# plus ISATAP (RFC 5214), matched by its interface-id signature below.
# Residual accepted risk (indistinguishable from ordinary global IPv6 without
# out-of-band knowledge, same tier as the DNS-rebinding TOCTOU): NAT64
# operator-chosen Network-Specific Prefixes (RFC 6052 §2.2) and 6rd
# operator prefixes (RFC 5969).
_IPV6_TRANSITION_PREFIXES = (
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("::/96"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("2001::/32"),
)

# ISATAP interface identifier (low 64 bits): 0000:5EFE:v4 (private/any v4) or
# 0200:5EFE:v4 (U/L bit set, public v4), under ANY routing prefix — so it is
# matched by bits 32–63 rather than by a prefix. (Caught in CRT177 round-3
# re-verify, Sonnet.)
_ISATAP_IID_SIGNATURES = (0x00005EFE, 0x02005EFE)


def _is_transition_ipv6(ip) -> bool:
    """True if `ip` is any IPv6 transition/embedding form that carries or
    tunnels an IPv4 (see _IPV6_TRANSITION_PREFIXES). Such forms are blocked
    wholesale for SSRF safety."""
    if not isinstance(ip, ipaddress.IPv6Address):
        return False
    if any(ip in net for net in _IPV6_TRANSITION_PREFIXES):
        return True
    if ((int(ip) >> 32) & 0xFFFFFFFF) in _ISATAP_IID_SIGNATURES:
        return True
    return False


def _is_private_ip(ip_str: str) -> bool:
    """SEC-008: True if the address must be blocked for SSRF.

    Two rules, both fail-safe:
      1. Any IPv6 transition/embedding form (see _is_transition_ipv6) — blocked
         outright, because is_global cannot see the embedded/tunnelled IPv4.
      2. Otherwise, allow only globally-routable addresses (is_global). This
         allowlist-by-classification covers private/RFC1918, loopback,
         link-local, cloud-metadata, reserved, unspecified, multicast, and
         CGNAT in one check — the old hand-rolled denylist missed 0.0.0.0/8,
         fe80::/10, and every mapped form (all confirmed bypasses, CRT177)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if _is_transition_ipv6(ip):
        return True
    return not ip.is_global


def _check_resolved_ips(hostname: str, url: str, label: str = "Webhook URL") -> None:
    """
    Resolve hostname and verify none of the returned IPs are private/reserved.

    Fails closed on NXDOMAIN. Called at both registration time (_validate_webhook_url)
    and send time (_send) to mitigate DNS rebinding attacks where a hostname resolves
    to a public IP at registration but is changed to an internal IP before dispatch.
    """
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(
            f"{label} hostname could not be resolved (DNS failure — rejecting for safety): "
            f"{hostname!r}: {exc}"
        ) from exc

    for addr_info in addr_infos:
        resolved_ip = addr_info[4][0]
        if _is_private_ip(resolved_ip):
            raise ValueError(
                f"{label} hostname {hostname!r} resolves to a private/reserved IP address "
                f"({resolved_ip}) — DNS rebinding attack rejected: {url!r}"
            )


def assert_url_public(url: str, label: str = "URL") -> None:
    """
    SEC-008: Reject a URL that could be used for SSRF — shared by the webhook
    dispatcher and the HTTP-lookup rule fetcher so both surfaces enforce the
    identical policy.

    Blocks:
    - Non-HTTP/HTTPS schemes (file://, ftp://, etc.)
    - Private / RFC 1918 IP addresses (both literal and DNS-resolved)
    - Loopback addresses
    - Cloud instance metadata endpoints (169.254.x.x)
    - DNS rebinding: hostname is resolved and all returned IPs are checked
    - Resolution failure: fails closed (NXDOMAIN → rejected)

    `label` is woven into the error messages so callers surface a
    domain-appropriate message (e.g. "Webhook URL", "Lookup URL"). Raises
    ValueError on any violation.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"Invalid {label}: {url!r}")

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"{label} scheme must be http or https, got {parsed.scheme!r}: {url!r}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"{label} has no hostname: {url!r}")

    # Block localhost by name
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        raise ValueError(f"{label} must not target localhost: {url!r}")

    # If the hostname is a literal IP address, classify it directly.
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None  # not an IP literal — fall through to DNS resolution

    if ip is not None:
        if _is_private_ip(str(ip)):
            raise ValueError(
                f"{label} targets a private/reserved IP address ({ip}): {url!r}"
            )
        # Valid globally-routable literal IP — no DNS resolution needed.
        return

    # SEC-008: DNS rebinding protection — resolve and check every returned IP.
    _check_resolved_ips(hostname, url, label)


def _validate_webhook_url(url: str) -> None:
    """Reject webhook URLs that could be used for SSRF attacks. See assert_url_public."""
    assert_url_public(url, label="Webhook URL")


class WebhookManager:
    """Manages webhook subscriptions and sends notifications on validation failures."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            import opendqv.config as config
            db_path = config.DB_PATH
        self.db_path = db_path
        self._mem_conn = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        self._hooks_cache: list[dict] | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Return a connection — shared for :memory:, new for file-based DBs."""
        if self._mem_conn is not None:
            return self._mem_conn
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        """Create the webhooks table if it doesn't exist."""
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS webhooks ("
            "url TEXT PRIMARY KEY, "
            "events TEXT, "
            "contracts TEXT)"
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()
        if self._mem_conn is None:
            conn.close()

    def register(self, url: str, events: list[str] = None, contracts: list[str] = None) -> dict:
        """
        Register a webhook.

        Args:
            url: The URL to POST notifications to.
            events: Event types to subscribe to. Defaults to all events.
                    Valid: "opendqv.validation.failed", "opendqv.validation.warning", "opendqv.batch.failed"
            contracts: Filter by contract name. None means all contracts.

        Returns:
            The registered hook dict.
        """
        _validate_webhook_url(url)

        if events is None:
            events = list(VALID_EVENTS)

        invalid = set(events) - VALID_EVENTS
        if invalid:
            raise ValueError(f"Invalid event types: {invalid}. Valid: {VALID_EVENTS}")

        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            conn.execute(
                "INSERT OR REPLACE INTO webhooks (url, events, contracts) VALUES (?, ?, ?)",
                (url, json.dumps(events), json.dumps(contracts) if contracts is not None else None),
            )
            conn.commit()
        finally:
            if not is_shared:
                conn.close()

        hook = {"url": url, "events": events, "contracts": contracts}
        self._hooks_cache = None
        logger.info("webhook registered: url=%s events=%s contracts=%s", url, events, contracts)
        return hook

    def unregister(self, url: str) -> bool:
        """Remove a webhook by URL. Returns True if found and removed."""
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            cursor = conn.execute("DELETE FROM webhooks WHERE url = ?", (url,))
            conn.commit()
            removed = cursor.rowcount > 0
        finally:
            if not is_shared:
                conn.close()
        if removed:
            self._hooks_cache = None
            logger.info("webhook unregistered: url=%s", url)
        return removed

    def list_hooks(self) -> list[dict]:
        """List all registered webhooks. Results are cached; cache is invalidated on register/unregister."""
        if self._hooks_cache is not None:
            return self._hooks_cache
        conn = self._connect()
        is_shared = self._mem_conn is not None
        try:
            rows = conn.execute("SELECT url, events, contracts FROM webhooks").fetchall()
        finally:
            if not is_shared:
                conn.close()
        hooks = []
        for url, events_json, contracts_json in rows:
            hooks.append({
                "url": url,
                "events": json.loads(events_json),
                "contracts": json.loads(contracts_json) if contracts_json is not None else None,
            })
        self._hooks_cache = hooks
        return hooks

    async def notify(self, event: str, payload: dict):
        """
        Send notification to all matching webhooks. Fire-and-forget — never raises.

        Filters hooks by event type and (optionally) contract name, then POSTs
        the payload to each matching URL concurrently.
        """
        contract_name = payload.get("contract")

        all_hooks = self.list_hooks()
        matching = [
            h for h in all_hooks
            if event in h["events"]
            and (h["contracts"] is None or contract_name in h["contracts"])
        ]

        if not matching:
            return

        envelope = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }

        tasks = [self._send(hook["url"], envelope) for hook in matching]
        await asyncio.gather(*tasks)

    async def _send(self, url: str, payload: dict):
        """POST payload to a single URL. Logs errors but never raises."""
        try:
            # SEC-008: Re-validate IP at send time to mitigate DNS rebinding.
            # A hostname may have resolved to a public IP at registration but the
            # DNS record could have changed to point at internal infrastructure
            # between registration and this dispatch.
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            try:
                ipaddress.ip_address(hostname)
                # Literal IP — already validated at registration; no re-check needed.
            except ValueError:
                # Hostname — re-resolve and re-check all returned IPs.
                # socket.getaddrinfo() is blocking; run in a thread so the
                # event loop is not stalled under high webhook volume.
                await asyncio.to_thread(_check_resolved_ips, hostname, url)

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
                logger.debug("webhook sent: url=%s status=%d", url, resp.status_code)
        except Exception as exc:
            logger.warning("webhook delivery failed: url=%s error=%s", url, exc)
