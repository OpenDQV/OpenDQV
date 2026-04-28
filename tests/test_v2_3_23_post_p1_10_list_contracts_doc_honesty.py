"""
v2.3.23 P1-10 — list_contracts docstring/description matches behaviour.

Persona B inside-view 2026-04-28: "active_count: 41 in metrics, but
list_contracts returned 43 entries, all status: active. Off-by-two."

Root cause: `list_contracts(include_all=False)` returns ALL non-
archived contracts (active + draft + review). The legacy docstring
said "By default only ACTIVE contracts" — wrong. Reviewer was
correct that the discrepancy was real; the explanation was
undocumented.

Sonnet's pre-impl review (a55ee05f0bdc2d9be): documentation-only fix.
Two edits — `core/contracts.py:list_contracts` docstring and
`routes_contracts.py:list_contracts` endpoint docstring + Query
description. No behavioral change. governance.active_count already
on /api/v1/stats — data is fully observable.
"""


class TestListContractsDocReality:
    """Behaviour-as-documented: list_contracts returns non-archived
    entries (active + draft + review). The recurrence test asserts
    the docstring text reflects this so a future refactor doesn't
    re-introduce the off-by-two confusion silently."""

    def test_list_contracts_returns_non_archived_by_default(self, client):
        """Behavioural anchor: list_contracts default response can
        contain status values other than 'active' (specifically
        'draft' or 'review'). At minimum, every entry must carry a
        status field so the consumer can filter."""
        r = client.get("/api/v1/contracts")
        assert r.status_code == 200, r.text
        contracts = r.json()
        assert len(contracts) > 0
        for c in contracts:
            assert "status" in c, f"contract entry missing status: {c}"
            # Default behaviour: ARCHIVED must NOT appear.
            assert c["status"] != "archived", (
                f"v2.3.23 P1-10: list_contracts default must exclude "
                f"archived. Got: {c}"
            )

    def test_core_list_contracts_docstring_describes_filter(self):
        """Sonnet's recurrence-test directive: assert the docstring
        text reflects actual filter behaviour. If a future refactor
        changes the default but doesn't update the docstring, this
        test catches it."""
        from opendqv.core.contracts import ContractRegistry
        doc = ContractRegistry.list_contracts.__doc__ or ""
        # Required: must mention "non-archived" or list the actual
        # statuses returned.
        lower = doc.lower()
        assert "non-archived" in lower or (
            "draft" in lower and "active" in lower and "review" in lower
        ), (
            f"v2.3.23 P1-10: ContractRegistry.list_contracts docstring "
            f"must accurately describe the default filter (non-archived: "
            f"active + draft + review). Got: {doc!r}"
        )

    def test_endpoint_description_explains_off_by_count_with_metrics(self):
        """The endpoint description should point consumers at
        governance.active_count for the active-only count, so a
        regulator-side reader doesn't see "off-by-two" between
        endpoints and reach for a bug report."""
        # Inspect the FastAPI route's endpoint docstring via the
        # OpenAPI spec is the realistic check, but a simpler proxy
        # is to read the function's __doc__ directly.
        from opendqv.api.routes_contracts import list_contracts as endpoint_fn
        doc = endpoint_fn.__doc__ or ""
        lower = doc.lower()
        # Must mention the governance.active_count cross-reference OR
        # explicitly call out "off-by" framing so the reader knows
        # this isn't a bug.
        assert (
            "active_count" in doc
            or "non-archived" in lower
            or "draft" in lower and "review" in lower
        ), (
            f"v2.3.23 P1-10: list_contracts endpoint docstring must "
            f"explain the default filter (active + draft + review) and "
            f"point consumers at governance.active_count for the "
            f"active-only count. Got: {doc!r}"
        )

    def test_governance_counts_match_list_contracts_status_breakdown(
        self, client, auth_headers
    ):
        """Outcome-coupled regression: the sum of governance
        active_count + draft_count + review_count from /api/v1/stats
        must equal the count of non-archived entries from
        /api/v1/contracts. If they ever diverge again, that's a real
        bug — not a docstring problem."""
        contracts_resp = client.get("/api/v1/contracts")
        assert contracts_resp.status_code == 200
        contracts = contracts_resp.json()

        stats_resp = client.get(
            "/api/v1/stats?window_hours=24", headers=auth_headers
        )
        assert stats_resp.status_code == 200
        gov = stats_resp.json().get("governance", {})

        # Count by status from /api/v1/contracts.
        from collections import Counter
        status_counts = Counter(c.get("status") for c in contracts)
        non_archived_total = sum(
            v for s, v in status_counts.items() if s != "archived"
        )

        sum_governance = (
            gov.get("active_count", 0)
            + gov.get("draft_count", 0)
            + gov.get("review_count", 0)
        )

        assert non_archived_total == sum_governance, (
            f"v2.3.23 P1-10 outcome guard: list_contracts non-archived "
            f"({non_archived_total}, by status: {dict(status_counts)}) "
            f"must equal sum of governance counts "
            f"({sum_governance}, breakdown: {gov}). If this assertion "
            f"fires, the docstring fix isn't enough — there's a real "
            f"divergence between the two subsystems."
        )
