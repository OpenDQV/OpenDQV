"""Hand-written clean and warning-only rows for the conformance corpus (review S5).

Generated probes only ever violate a rule, so a corpus built from them alone
proves that the two engines agree on *rejection* and says nothing about
*acceptance*. These rows are authored by a human, one per bundled fixture
contract, and the generator (scripts/conformance_fixtures.py) refuses to
emit a corpus in which a ``clean`` row is not valid-with-no-warnings or a
``warning_only`` row is not valid-with-at-least-one-warning under Core.

Values are synthetic. No real person, account or organisation is described.
Contracts whose rules are all error-severity (dora_ict_incident) have no
warning-only row; the test only demands one where a warning rule exists.

Note on dora_ict_incident: since the golden-library sync (2.7.0) every DORA
timestamp field requires a full ISO 8601 datetime WITH a zone designator, and
the reporting clock runs classification → initial notification (≤4h) →
intermediate report (≤72h of the initial) → final report (≤1 month of the
intermediate) per CDR (EU) 2024/1773; the row below sits inside every window.
"""

from __future__ import annotations

CLEAN_ROWS: dict[str, list[dict]] = {
    "banking_transaction": [
        {
            "transaction_id": "TXN-2026-000001",
            "account_number": "ACC12345678",
            "sort_code": "12-34-56",
            "bic": "DEUTDEFF",
            "transaction_date": "2026-01-15",
            "amount": 100.5,
            "currency": "GBP",
            "transaction_type": "credit",
            "channel": "online",
            "reference": "INV-2026-1001",
            "merchant_id": "MERCH-001",
        },
    ],
    "hr_employee": [
        {
            "employee_id": "EMP-0001",
            "first_name": "Alex",
            "last_name": "Example",
            "email": "alex.example@example.com",
            "ni_number": "AB123456C",
            "department": "Engineering",
            "job_title": "Data Engineer",
            "start_date": "2026-01-05",
            "salary": 45000,
            "salary_currency": "GBP",
            "contract_type": "permanent",
            "employment_status": "active",
            "right_to_work_status": "verified_british_irish",
        },
    ],
    "customer": [
        {
            "id": "cust-0001",
            "email": "alex.example@example.com",
            "age": 30,
            "balance": 100.0,
            "phone": "+447700900123",
            "name": "Alex Example",
            "score": 50,
            "date": "2026-01-01",
            "username": "alex_01",
            "password": "correct-horse-battery",
            "loyalty_tier": "gold",
        },
    ],
    "nhs_dsp_patient": [
        {
            "patient_id": "PAT-0001",
            "first_name": "Alex",
            "last_name": "Example",
            "date_of_birth": "1980-01-01",
            "nhs_number": "9434765919",
            "sex": "male",
            "ethnicity": "white_british",
            "admission_date": "2026-01-01",
            "admission_type": "elective",
            "ward": "Ward 4",
            "diagnosis_code": "J18.9",
            "blood_type": "O+",
            "discharge_date": "2026-01-05",
            "discharge_reason": "clinical_discharge",
        },
    ],
    "dora_ict_incident": [
        {
            "incident_id": "INC-2026-0001",
            "incident_title": "Failover misconfiguration in payments gateway",
            "entity_name": "Example Credit Institution",
            "entity_type": "credit_institution",
            "incident_classification": "major",
            "incident_description": "Payments gateway failed over to a stale configuration.",
            "affected_services": "card_payments",
            "detection_timestamp": "2026-01-10T08:00:00Z",
            "major_classification_timestamp": "2026-01-10T09:30:00Z",
            "initial_notification_timestamp": "2026-01-10T10:00:00Z",
            "intermediate_report_timestamp": "2026-01-11T10:00:00Z",
            "root_cause": "Stale failover configuration",
            "remediation_status": "resolved",
            "remediation_date": "2026-01-12",
            "final_report_date": "2026-01-20",
            "reviewed_by": "Resilience Lead",
            "review_date": "2026-01-13",
        },
    ],
}

# Valid records that trip at least one warning-severity rule and no error.
WARNING_ONLY_ROWS: dict[str, list[dict]] = {
    "banking_transaction": [
        {**CLEAN_ROWS["banking_transaction"][0], "amount": 90000},  # amount_max (warning)
    ],
    "hr_employee": [
        {**CLEAN_ROWS["hr_employee"][0], "salary": 9000},  # salary below living-wage floor (warning)
    ],
    "customer": [
        {**CLEAN_ROWS["customer"][0], "age": 200},  # age_max (warning)
    ],
    "nhs_dsp_patient": [
        {**CLEAN_ROWS["nhs_dsp_patient"][0], "admission_type": "unscheduled"},  # allowed_values (warning)
    ],
    "dora_ict_incident": [],  # every DORA rule is error-severity
}
