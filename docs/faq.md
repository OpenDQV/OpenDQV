# Frequently Asked Questions

---

## Can't I just prompt Claude / GPT / Cursor to build validation logic in 15 minutes?

Yes — and many teams start exactly that way.

What tends to happen next in real organisations:

- The script works until the author leaves, the requirements change, or compliance asks for an audit trail
- Every new source system (Salesforce, SAP, Kafka, Postgres) needs a new prompt — new edge cases, new bugs, new drift
- No central governance: rules live in scattered notebooks, functions, or one-off classes
- No maker-checker workflow, no Prometheus rejection metrics, no context overrides (production vs sandbox), no importers from your existing GX or Soda suite
- When a regulator asks "prove this rule was applied and approved before it went live" — there is no answer

OpenDQV turns that one-off script into a **governed, versioned contract** that lives in one YAML file, generates code for any target (Apex, JavaScript, Postgres trigger, Snowflake UDF, or API call), and enforces the same rules everywhere — with immediate 422 feedback at write time.

It is not "instead of prompting an LLM." It is **what you do with the good prompt result** so it survives the next re-org, the next outsourcing cycle, and the next compliance audit.

---

## Why not use Great Expectations, Soda Core, or dbt tests?

These are excellent tools — and they solve a different problem.

| | Great Expectations / Soda / dbt tests | OpenDQV |
|---|---|---|
| **When it runs** | After data lands in your warehouse | Before data enters any system |
| **What it checks** | Tables, datasets, historical distributions | Individual records at the point of write |
| **Failure response** | Alert, report, pipeline failure | 422 returned to the caller with per-field errors |
| **Compute cost** | Full or sampled table scans inside the warehouse | Sub-second API call or UDF — no warehouse needed |
| **Governance** | Test results | Versioned contracts with maker-checker approval and hash-chained audit trail |

OpenDQV Core is Layer 1. GE/Soda/dbt are Layer 3. They are designed to work together. See the [three-layer model](../README.md#who-is-this-for) in the README.

---

## What about hundreds of outsourced stored procedures?

This is still the reality in many large enterprises: an SI wrote 400+ PL/SQL or T-SQL procedures for data quality checks. No version control, no audit trail, no governance, no central ownership.

OpenDQV replaces the entire proc factory with one version-controlled contract per entity. The same contract that enforces rules via API can also generate native code for systems that cannot make HTTP calls — currently Snowflake UDFs, Salesforce Apex, and JavaScript. Community-contributed generators for other platforms (Postgres, SQL Server, etc.) are welcome via PR. Migrate incrementally, one contract at a time, with no big-bang cutover required.

The contract lifecycle (draft → review → active → archived) ensures every rule change is proposed, reviewed, and approved before it affects production — governance that legacy stored-procedure approaches rarely include out of the box.

---

## Does OpenDQV help with Databricks or Snowflake migrations?

Yes — and this is one of the most overlooked use cases.

Enterprise cloud platform migrations frequently stall not because of technology, budget, or executive will — but because DQ logic is buried in hundreds of platform-specific stored procedures that only a small team understands. The procedures are hard to migrate, the knowledge is not documented, and the timeline slips.

OpenDQV removes that dependency. Replace the stored procedures with portable YAML contracts once — and the underlying platform becomes interchangeable. The same contracts run via API or generate native Snowflake UDFs. The DQ layer no longer ties you to a specific warehouse.

---

## Does OpenDQV replace my data catalog?

No. OpenDQV Core is Layer 1 (write-time enforcement). Your data catalog (Alation, Collibra, Atlan, DataHub, Purview) is Layer 2 (ownership, lineage, glossary, stewardship). They are complementary.

Think of OpenDQV as the enforcement layer that sits upstream of everything your catalog manages — it ensures the data being governed was clean before it arrived.

---

## Is this production-ready?

Yes. OpenDQV Core is MIT-licensed, published to PyPI (`pip install opendqv`), ships a multi-arch Docker image, has 2,000+ passing tests, an OpenSSF Best Practices badge (100%), and an OpenSSF Scorecard of 6.5+. It has been validated on Linux (x86-64), macOS (Intel), Windows (Docker Desktop), and Raspberry Pi 400 (ARM64).

See the [benchmark results](benchmark_throughput.md) for throughput figures across all platforms.

---

## Where do I start?

The fastest path: **[Quickstart guide](quickstart.md)** — zero to first validation in 15 minutes.

Or use the onboarding wizard:

```bash
git clone https://github.com/OpenDQV/OpenDQV.git
cd OpenDQV
bash install.sh   # Mac/Linux — starts the wizard automatically
```
