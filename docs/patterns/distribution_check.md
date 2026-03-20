# Distribution Check — Recommended Pattern

## Why distribution_check is not a rule type

OpenDQV validates the quality of individual records against declarative contracts. A distribution check (e.g., "age must follow a normal distribution", "category A must appear in 30-40% of records") operates on aggregate statistics across the batch, not on individual record validity.

Building distribution checks into OpenDQV would require:
- Storing summary statistics across batches
- A statistical model separate from the rule definition
- Configuration that changes over time as baselines drift

This is outside OpenDQV's design philosophy: per-record, ephemeral, deterministic validation.

## Recommended: OpenDQV + Evidently

> **API last verified:** `evidently v0.7.21` — 2026-03-13.
> Snippets are examples; pin your own version in `requirements.txt`.
> [Check for updates](https://pypi.org/project/evidently/)

Pair OpenDQV with [Evidently AI](https://www.evidentlyai.com/) for distribution monitoring:

| Tool | Responsibility |
|------|--------------|
| OpenDQV | Per-record rule validation — format, range, referential integrity, checksums |
| Evidently | Batch statistical monitoring — distribution drift, data skew, column correlation |

```python
# Example: OpenDQV validates records, Evidently monitors distributions
# pip install evidently pandas
import pandas as pd
from evidently import Report, Dataset, DataDefinition
from evidently.metrics import DataDriftPreset

# 1. Validate individual records with OpenDQV
validated = [validate_record(r, rules) for r in records]

# 2. Monitor distribution drift with Evidently (v0.7+ API)
reference_data = pd.read_parquet("reference_batch.parquet")
current_data = pd.DataFrame(records)
definition = DataDefinition()  # auto-infers column types; customise if needed
reference_dataset = Dataset.from_pandas(reference_data, data_definition=definition)
current_dataset = Dataset.from_pandas(current_data, data_definition=definition)
report = Report([DataDriftPreset()])
snapshot = report.run(current_dataset, reference_dataset)
snapshot.save_html("drift_report.html")
```

> **v0.7 migration note:** `ColumnMapping` was removed in Evidently v0.7 (April 2025) — use `DataDefinition` instead. `report.run()` now returns a `Snapshot` object; call `save_html` on the snapshot, not the report. See the [Evidently migration guide](https://docs.evidentlyai.com/faq/migration).
