# Information Quality Dimension Mapping

*Aligned with ISO/IEC 25012 data quality terminology.*

---

## Background

OpenDQV uses practitioner terminology ("not_empty", "regex", "range", etc.) that is operationally clear but does not map explicitly to the formal Information Quality (IQ) vocabulary used in ISO 8000, ISO/IEC 25012, and the Wang & Strong (1996) framework.

This document provides a **canonical mapping** so that:
- Academic researchers can interpret OpenDQV results in IQ framework terms
- Regulatory submissions (EMA, MiFIR, BCBS 239) can reference the correct IQ dimensions
- Data governance teams can trace rule failures to specific quality objectives

---

## ISO/IEC 25012 Dimensions Supported by OpenDQV

| ISO/IEC 25012 Dimension | Definition | OpenDQV Rule Types |
|---|---|---|
| **Completeness** | Degree to which all required data is present | `not_empty`, `required_if`, `forbidden_if` |
| **Accuracy** | Degree to which data correctly describes the real-world entity | `regex`, `allowed_values`, `checksum`, `range` |
| **Consistency** | Degree to which data does not contradict other data | `cross_field_range`, `field_sum`, `conditional_value`, `compare` |
| **Credibility** | Degree to which data is regarded as true and believable | `checksum` (IBAN, NHS, ISIN, GTIN, VIN) |
| **Currentness** | Degree to which data is sufficiently up-to-date | `compare` with `compare_to: today/now`, `date_diff` |
| **Accessibility** | Degree to which data can be retrieved | Covered by API availability (SLA) |
| **Compliance** | Degree to which data adheres to standards | `regex` with standards patterns, `checksum`, `allowed_values` with ISO codes |
| **Confidentiality** | Degree to which data is accessible only to authorised users | `sensitive_fields` mask/hash in trace log |
| **Efficiency** | Degree to which data processing does not waste resources | Batch endpoint, DuckDB engine |
| **Precision** | Degree to which data has detail sufficient for the intended use | `range` (min/max precision bounds) |
| **Traceability** | Degree to which data history can be audited | Contract history, `?as_of=`, `engine_version` in response |
| **Understandability** | Degree to which data has attributes that enable easy interpretation | `error_message` in rules, `description` on contracts |
| **Availability** | Degree to which data is retrievable when required | Health endpoint, federation status |
| **Portability** | Degree to which data can be transferred across systems | ODCS export, dbt/GX importers |
| **Recoverability** | Degree to which data maintains required quality after modification | Contract history, `?as_of=` point-in-time |

---

## Wang & Strong (1996) Framework Mapping

Wang & Strong identify 15 IQ dimensions in 4 categories. Below is the mapping to OpenDQV:

### Intrinsic IQ

| Wang & Strong Dimension | OpenDQV Coverage |
|---|---|
| **Accuracy** | `regex`, `allowed_values`, `checksum`, `range` |
| **Objectivity** | N/A — requires human judgment |
| **Believability** | `checksum` (algorithmic credibility verification) |
| **Reputation** | N/A — organizational trust dimension |

### Contextual IQ

| Wang & Strong Dimension | OpenDQV Coverage |
|---|---|
| **Relevancy** | `context` overrides — rules active per use-case |
| **Value-added** | ROI calculator (`docs/roi_calculator.md`) |
| **Timeliness** | `compare` with `compare_to: today/now`; `date_diff` |
| **Completeness** | `not_empty`, `required_if` |
| **Appropriate amount** | `range` (min/max field values) |

### Representational IQ

| Wang & Strong Dimension | OpenDQV Coverage |
|---|---|
| **Interpretability** | `error_message` on each rule |
| **Ease of understanding** | Human-readable YAML contract format |
| **Representational consistency** | `allowed_values`, `regex` with canonical patterns |
| **Concise representation** | N/A — schema design concern |

### Accessibility IQ

| Wang & Strong Dimension | OpenDQV Coverage |
|---|---|
| **Accessibility** | REST API, Python SDK, federation |
| **Access security** | PAT auth, `sensitive_fields` masking |

---

## DAMA-DMBOK2 Dimension Mapping

| DAMA Dimension | OpenDQV Coverage |
|---|---|
| **Completeness** | `not_empty`, `required_if` |
| **Validity** | `regex`, `allowed_values`, `range`, `checksum` |
| **Uniqueness** | `unique` (with optional `group_by`) |
| **Timeliness** | `compare` with today/now sentinels |
| **Consistency** | `cross_field_range`, `field_sum`, `conditional_value` |
| **Accuracy** | `checksum`, `regex` with standards |
| **Integrity** | `forbidden_if`, `required_if` (referential constraints) |

---

## Severity → Impact Mapping

OpenDQV uses two severity levels. Their IQ dimension equivalents:

| Severity | Description | IQ Impact |
|---|---|---|
| `error` | Blocking — record fails validation | Completeness, Accuracy, Validity failures |
| `warning` | Non-blocking — quality advisory | Timeliness, Precision, Representational issues |

---

## Rule-Level IQ Tags (Optional)

Contract authors may annotate rules with IQ dimension tags for governance reporting:

```yaml
rules:
  - name: email_format
    type: regex
    field: email
    pattern: "^[^@]+@[^@]+\\.[^@]+$"
    error_message: Invalid email format
    # Optional metadata for IQ dimension reporting:
    iq_dimensions: [accuracy, validity]

  - name: date_not_future
    type: compare
    field: transaction_date
    compare_to: today
    compare_op: lte
    error_message: Transaction date cannot be future-dated
    iq_dimensions: [timeliness, accuracy]
```

These tags are passed through in validation responses and can be used by downstream BI tools to aggregate quality failures by IQ dimension.

---

## References

- Wang, R. Y., & Strong, D. M. (1996). Beyond accuracy: What data quality means to data consumers. *Journal of Management Information Systems*, 12(4), 5–33.
- ISO/IEC 25012:2008 — Software engineering — Software product quality requirements and evaluation — Data quality model.
- ISO 8000-8:2015 — Data quality — Part 8: Information and data quality: Concepts and measuring.
- DAMA International. (2017). *DAMA-DMBOK: Data Management Body of Knowledge* (2nd ed.).
