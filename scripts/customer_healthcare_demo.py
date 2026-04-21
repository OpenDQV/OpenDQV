#!/usr/bin/env python3
"""
Customer Healthcare demo — validates patient records against nhs_dsp_patient
(NHS Data Dictionary, Caldicott Principles, ICD-10) and prints a pass/fail summary.

Usage:
    python scripts/customer_healthcare_demo.py --customer <name>
    OPENDQV_CUSTOMER=<name> python scripts/customer_healthcare_demo.py

Customer-specific records live in scripts/healthcare_demo_customers.local.json (gitignored).
Format:
    {
      "ACME": [
        ["Procedure Name", "PAT-001", "elective", null, {}],
        ...
      ]
    }

fail_mode options: null, "blood_type_no_rhesus", "discharge_before_admission",
                   "invalid_icd10", "no_ward"

Records persist with context='demo'. Run scripts/teardown_demo.py to clean up.
"""
import argparse
import os
from datetime import date

from _demo_utils import _load_menu, run_demo

_today = date.today().isoformat()

_DEFAULT_MENU = [
    # Passes
    ("Elective Hip Replacement",     "PAT-001", "elective",   None,                    {}),
    ("Routine Blood Panel",          "PAT-002", "day_case",   None,                    {}),
    # Failures — four instructive clinical data quality gaps
    ("Emergency Appendectomy",       "PAT-003", "emergency",  "blood_type_no_rhesus",  {}),
    ("Day Surgery — Cataracts",      "PAT-004", "day_case",   "discharge_before_admission", {}),
    ("Outpatient Consultation",      "PAT-005", "elective",   "invalid_icd10",         {}),
    ("Maternity Assessment",         "PAT-006", "maternity",  "no_ward",               {}),
]

_FAIL_LABELS = {
    "blood_type_no_rhesus":        "blood_type: must include Rhesus factor (e.g. AB+ not AB) — NHS Never Event risk",
    "discharge_before_admission":  "discharge_date must be on or after admission_date — corrupts NHS LoS reporting",
    "invalid_icd10":               "diagnosis_code: invalid ICD-10 format — rejected by NHS SUS+ data warehouse",
    "no_ward":                     "ward: required",
}


def build_healthcare_record(procedure: str, patient_id: str, admission_type: str,
                             fail_mode, overrides: dict) -> dict:
    """Build a valid nhs_dsp_patient record then apply the requested failure mode."""
    record: dict = {
        "patient_id":       patient_id,
        "first_name":       "A",
        "last_name":        "Patient",
        "date_of_birth":    "1975-06-15",
        "nhs_number":       "123-456-7890",
        "sex":              "not_stated",
        "ethnicity":        "not_stated",
        "admission_date":   _today,
        "admission_type":   admission_type,
        "ward":             "General Ward A",
        "diagnosis_code":   "Z00.0",
        "blood_type":       "O+",
        "discharge_date":   _today,
        "discharge_reason": "clinical_discharge",
    }
    record.update(overrides)

    if fail_mode == "blood_type_no_rhesus":
        record["blood_type"] = "AB"  # fails regex ^(A|B|AB|O)[+-]$

    elif fail_mode == "discharge_before_admission":
        record["admission_date"]  = "2026-01-10"
        record["discharge_date"]  = "2026-01-05"  # before admission

    elif fail_mode == "invalid_icd10":
        record["diagnosis_code"] = "Z9999"  # too many digits — invalid ICD-10

    elif fail_mode == "no_ward":
        record.pop("ward", None)

    return record


def run(customer: str) -> None:
    menu = _load_menu("healthcare", customer, _DEFAULT_MENU)
    run_demo("Healthcare Patient", "nhs_dsp_patient", customer, menu,
             build_healthcare_record, _FAIL_LABELS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OpenDQV Healthcare patient customer demo"
    )
    parser.add_argument(
        "--customer",
        default=os.environ.get("OPENDQV_CUSTOMER", "demo"),
        help="Customer name. Loaded from healthcare_demo_customers.local.json if present.",
    )
    args = parser.parse_args()
    run(args.customer)
