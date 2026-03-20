"""
Error remediation loop — validate → explain → fix (Claude API) → re-validate → write.

This is the agentic pattern from docs/llm_integration.md §5.
Run it directly to see the full loop with a realistic broken record:

    cd ~/OpenDQV && source .venv/bin/activate
    pip install anthropic          # only needed here, not in OpenDQV core
    ANTHROPIC_API_KEY=sk-... python scripts/llm_remediation_loop.py

Requires:
    - OpenDQV API running on OPENDQV_URL (default http://localhost:8000)
    - ANTHROPIC_API_KEY environment variable set
"""

import json
import os

import requests

OPENDQV_URL = os.environ.get("OPENDQV_URL", "http://localhost:8000")
MAX_RETRIES = 2


def fix_with_claude(record: dict, hints: list[dict]) -> dict:
    """
    Call Claude to correct a record that failed validation.

    hints — list of {field, message, explanation, valid_examples} dicts,
    one per failing rule, sourced directly from OpenDQV's explain_error endpoint.
    Claude receives the exact constraint details and concrete valid examples,
    making self-correction reliable rather than guesswork.
    """
    import anthropic  # imported here so the module loads without the package installed

    claude = anthropic.Anthropic()

    hints_text = "\n".join(
        f"- field '{h['field']}': {h['explanation']} "
        f"(valid examples: {h['valid_examples']})"
        for h in hints
    )
    prompt = (
        f"The following JSON record failed data validation:\n"
        f"```json\n{json.dumps(record, indent=2)}\n```\n\n"
        f"Validation errors and how to fix them:\n{hints_text}\n\n"
        "Return ONLY the corrected JSON record with no explanation or markdown. "
        "Do not add, remove, or rename fields — only correct the values that failed."
    )
    response = claude.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()
    # Strip accidental markdown code fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def validate_and_fix(contract: str, record: dict) -> dict:
    """
    Validate → explain → fix → re-validate loop.

    Returns:
        {"status": "ok",       "record": {...}, "attempts": int}  — clean record
        {"status": "escalate", "record": {...}, "errors": [...]}  — still failing; hand to human
    """
    last_errors = []
    for attempt in range(MAX_RETRIES + 1):
        result = requests.post(
            f"{OPENDQV_URL}/api/v1/validate",
            json={"contract": contract, "record": record},
        ).json()

        if result["valid"]:
            return {"status": "ok", "record": record, "attempts": attempt + 1}

        last_errors = result["errors"]

        if attempt == MAX_RETRIES:
            return {
                "status": "escalate",
                "record": record,
                "errors": last_errors,
                "message": f"Still failing after {MAX_RETRIES + 1} attempts — escalate to human",
            }

        # Fetch plain-English remediation hints for every failing rule
        hints = []
        for error in last_errors:
            explanation = requests.get(
                f"{OPENDQV_URL}/api/v1/contracts/{contract}"
                f"/explain/{error['field']}/{error['rule']}"
            ).json()
            hints.append({
                "field": error["field"],
                "message": error["message"],
                "explanation": explanation.get("explanation", ""),
                "valid_examples": explanation.get("valid_examples", []),
            })

        # Claude fixes the record using the constraint-aware hints
        record = fix_with_claude(record, hints)

    return {"status": "escalate", "record": record, "errors": last_errors}


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Realistic upstream bug: amount sent as currency string "£250.00"
    # instead of a float. Without validation it silently corrupts the ledger.
    bad_record = {
        "transaction_id": "TXN-2026-0311-001",
        "account_number": "40158793",
        "transaction_date": "2026-03-11",
        "amount": "£250.00",          # ← upstream bug: string instead of float
        "currency": "GBP",
        "transaction_type": "transfer",
        "channel": "mobile",
        "merchant_id": "MCHT-00124",
        "merchant_category_code": "5411",
    }

    print(f"\nInput record:\n{json.dumps(bad_record, indent=2)}\n")
    outcome = validate_and_fix("banking_transaction", bad_record)

    if outcome["status"] == "ok":
        print(f"✓ Clean after {outcome['attempts']} attempt(s) — safe to write")
        print(json.dumps(outcome["record"], indent=2))
    else:
        print("✗ Could not auto-fix — routing to human review queue")
        for e in outcome["errors"]:
            print(f"  {e['field']}: {e['message']}")
