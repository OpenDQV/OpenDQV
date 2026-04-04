# Streamlit Workbench

The OpenDQV Streamlit Workbench is a developer and governance UI for managing contracts,
testing validations, and monitoring data quality.

---

## Starting the workbench

```bash
# Standalone (no Docker)
streamlit run ui/app.py
# Opens at http://localhost:8501

# Via Docker Compose (auto-started with the stack)
docker compose up -d
# Opens at http://localhost:8501
```

---

## Sections

| Section | Purpose |
|---------|---------|
| **Contracts** | Browse contracts, view rules, manage lifecycle (draft → review → active → archived) |
| **Validate** | Test single records or batches interactively with any contract + context |
| **Monitoring** | Live validation pass/fail rates, top failing fields, recent activity |
| **Audit Trail** | Contract version history, hash-chain integrity, governance approvals |
| **Catalogs & AI** | External catalog deep-links (Marmot, DataHub, Atlan) + MCP agent prompts |
| **Integration Guide** | Generate ready-to-paste code snippets for every platform |
| **Code Export** | Generate embedded validation code (push-down mode) |
| **Import Rules** | Import contracts from GX, dbt, Soda, Monte Carlo, or Data Contract CLI |
| **Profiler** | Analyze a sample dataset and auto-generate a suggested contract |
| **Webhooks** | Register and manage HTTP webhooks for validation events |
| **Federation** | Node health, federation status, and event log for the OpenDQV network layer |
| **CLI Guide** | Command-line reference and usage examples |

---

## Monitoring tab

Shows live statistics from the in-memory validation store:

- Total validations, pass/fail counts, pass rate
- Per-contract and per-context breakdown
- Top failing fields and rules (ranked by failure count)
- Validation latency over time

Data resets on server restart. For persistent dashboards use the Prometheus metrics endpoint
at `/metrics` — see [docs/observability.md](observability.md).

---

## Validate tab

Interactive record testing:

1. Select a contract from the dropdown
2. Select a context (optional)
3. Paste a JSON record or fill in fields
4. Click **Validate** — results show per-field errors and warnings inline

---

## Import Rules tab

Import contracts from other tools:

- **Great Expectations** — paste a GX expectation suite JSON
- **dbt** — paste or upload a `schema.yml`
- **Soda Core** — paste a `checks for <dataset>:` YAML block
- **CSV** — upload a spreadsheet-style rule CSV

See [docs/importers.md](importers.md) for format documentation and examples.

---

## Code Export tab (push-down mode)

Generate validation logic to embed directly in systems that can't make HTTP calls:

- Select a contract and target platform (Salesforce Apex / JavaScript / Snowflake UDF)
- Click **Generate** — copy the output code directly into your system

See [docs/code_generation.md](code_generation.md) for the full reference.

---

## Catalogs & AI tab

Provides deep-links to external data catalog tools when catalog integration is configured:

- **Marmot** — click to open the lineage diagram for any contract
- **DataHub**, **Atlan**, **Collibra**, **OpenMetadata** — deep-links when configured

Set `MARMOT_URL` (and equivalent) in `.env` to enable catalog deep-links.

---

## Rebuilding after UI changes

The Docker Compose dev stack does **not** mount `ui/` as a volume. After any change to
`ui/app.py`, rebuild the UI image:

```bash
docker compose build ui && docker compose up -d --no-deps ui
```

---

## Related

- [Quickstart](quickstart.md) — first validation in 15 minutes
- [Observability](observability.md) — Prometheus metrics and persistent dashboards
- [Code Generation](code_generation.md) — push-down validation for offline systems
- [Importers](importers.md) — importing rules from other tools
