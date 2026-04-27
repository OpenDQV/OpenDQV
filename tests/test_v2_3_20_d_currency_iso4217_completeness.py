"""
v2.3.20 Cluster D (P1.1) — currency lookup completeness.

Persona B 2026-04-27 outside-review P1.1:
> Currency lookup advertises "ISO 4217" but only accepts 12 codes in
> mifid_transaction_report. HKD, SGD, ZAR, NZD, AED — all valid ISO 4217
> — are rejected. Customer impact: a global trading desk gets false
> rejections on legitimate Asia/EMEA currencies.

Sonnet's pre-impl review pinpointed this as a CONTENT fix not an
architecture fix: the rule already uses ``lookup_file: ref/universal_currency.txt``
with no inline ``values:`` co-present. The file just had only 12 codes.
The fix is to expand the file to the full ISO 4217 active list.

This test asserts the explicit currencies the reviewer named PASS, plus
a sample of other major currencies including all G10 + commonly-traded
EMEA / Asia / LATAM / commodity currencies.
"""

from pathlib import Path

import pytest

from opendqv.core.contracts import ContractRegistry
from opendqv.core.validator import validate_record


@pytest.fixture(scope="module")
def mifid_contract():
    reg = ContractRegistry(Path("opendqv/contracts"))
    return reg.get("mifid_transaction_report")


def _record_with_currency(code: str) -> dict:
    return {
        "transaction_reference": f"TXN-CUR-{code}",
        "execution_timestamp": "2026-04-26T10:00:00.000000Z",
        "trade_date": "2026-04-26",
        "reporting_firm_lei": "529900T8BM49AURSDO55",
        "executing_entity_lei": "529900T8BM49AURSDO55",
        "venue_mic": "XLON",
        "instrument_isin": "GB00B03MLX29",
        "buyer_id_type": "lei",
        "buyer_id": "529900T8BM49AURSDO55",
        "seller_id_type": "lei",
        "seller_id": "529900T8BM49AURSDO55",
        "price": 100.5,
        "price_type": "monetary",
        "currency": code,
        "quantity": 1000,
        "quantity_type": "units",
        "buy_sell_indicator": "BUYI",
        "investment_decision_within_firm": "529900T8BM49AURSDO55",
        "execution_within_firm": "529900T8BM49AURSDO55",
        "transaction_type": "buy",
        "reviewed_by": "ops-alice",
        "review_date": "2026-04-26",
    }


class TestCurrencyIso4217Completeness:
    @pytest.mark.parametrize("code", [
        # Reviewer's exact named currencies
        "HKD", "SGD", "ZAR", "NZD", "AED",
        # G10
        "USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "SEK", "NOK", "NZD",
        # Asia
        "CNY", "INR", "KRW", "TWD", "THB", "IDR", "MYR", "PHP", "VND",
        # EMEA
        "TRY", "PLN", "CZK", "HUF", "RON", "ILS", "SAR", "QAR", "EGP",
        # LATAM
        "BRL", "MXN", "ARS", "CLP", "COP", "PEN",
        # Africa
        "NGN", "KES", "GHS", "MAD",
    ])
    def test_currency_passes_currency_valid(self, mifid_contract, code):
        result = validate_record(
            _record_with_currency(code),
            mifid_contract.rules,
            contract_name="mifid_transaction_report",
        )
        currency_errs = [e for e in result["errors"] if e["rule"] == "currency_valid"]
        assert not currency_errs, (
            f"v2.3.20 P1.1 regression: {code!r} (a valid ISO 4217 active code) "
            f"rejected by currency_valid lookup. Reviewer's exact failure mode."
        )

    def test_invalid_code_still_rejected(self, mifid_contract):
        """Sanity: expanding the list must not break rejection of bogus codes."""
        result = validate_record(
            _record_with_currency("XYZ"),  # Not a valid ISO 4217 code
            mifid_contract.rules,
            contract_name="mifid_transaction_report",
        )
        currency_errs = [e for e in result["errors"] if e["rule"] == "currency_valid"]
        assert currency_errs, "bogus 'XYZ' code must still fail currency_valid"

    def test_currency_lookup_file_has_at_least_150_codes(self):
        """Floor check: ISO 4217 has 180+ active codes; the bundled file
        should have at least 150 to cover the realistic regulated-FS
        trading universe."""
        path = Path("opendqv/contracts/ref/universal_currency.txt")
        codes = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        assert len(codes) >= 150, (
            f"universal_currency.txt has only {len(codes)} entries; "
            f"v2.3.20 P1.1 fix requires at least 150 to cover ISO 4217 "
            f"active codes. Reviewer specifically named HKD, SGD, ZAR, NZD, "
            f"AED — all must be present."
        )
        # The reviewer's exact named codes must be present
        for required in ("HKD", "SGD", "ZAR", "NZD", "AED"):
            assert required in codes, f"reviewer's named code {required} missing"
