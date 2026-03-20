# Zero to First Validation in 15 Minutes

Welcome. This guide will take you from a fresh install to validating your first data record — no engineering background required, no configuration files to wrestle with.

By the end, you will have OpenDQV running locally and will have validated a record against a pre-built contract. The whole thing takes about 15 minutes.

---

> **You need:** Docker Desktop ([download here](https://www.docker.com/products/docker-desktop/)) or Python 3.11+. That's it.

---

## Step 1 — Get the code (2 min)

If you are comfortable with a terminal, clone the repository:

```bash
git clone https://github.com/OpenDQV/OpenDQV.git
cd OpenDQV
cp .env.example .env
```

If you prefer not to use a terminal, download the release zip from the GitHub releases page and unzip it. Then make a copy of the file called `.env.example` and name it `.env`. You do not need to change anything inside it for local use.

---

## Step 2 — Start OpenDQV

**If you have Docker:** this is the fastest path.

```bash
docker compose up
```

Docker will download the necessary components (about 400 MB on the first run — grab a coffee) and start both services. When ready you will see:

```
✔ Container opendqv-api-1       Started
✔ Container opendqv-workbench-1 Started
```

**If you don't have Docker (Python 3.11+ only):** use the bootstrap script. The first run takes 2–3 minutes to install dependencies.

Mac/Linux:
```bash
bash install.sh
```

Windows:
```bat
install.bat
```

The script creates an isolated virtual environment, installs dependencies, and launches the onboarding wizard, which will:
1. Check your environment and start the API
2. Ask you which industry template to start with (or let you build your own)
3. Validate a sample record against your chosen contract and show the result

When it finishes, you are up and running. The workbench is at **http://localhost:8501** and the API is at **http://localhost:8000**.

---

## Explore further (optional)

Once the wizard has the API running, you can explore the visual workbench and try the API directly.

### Open the workbench

Open your browser and go to **http://localhost:8501**.

You will see the OpenDQV workbench — a visual interface for exploring contracts and validating records. No login is required for local use.

On the left, you will find a sidebar with navigation sections. Click on **Contracts**. You will see industry templates already loaded — things like `customer`, `banking_transaction`, `healthcare_patient`, and more. Click any contract to browse its validation rules and see what fields it expects.

Take a moment to explore. Each rule has a plain-English description of what it checks.

### Validate a record manually

#### Option A — No code (workbench)

1. In the sidebar, click **Validate Record**
2. Select the `customer` contract from the dropdown
3. Paste this sample JSON into the input box:

```json
{
  "id": "cust-001",
  "name": "Joe Bloggs",
  "email": "joe@example.com",
  "age": 32
}
```

4. Click **Validate**

You will see a green **Valid** result along with a breakdown showing which rules passed. Try changing the email to something invalid (remove the `@` sign) and validate again — you will see exactly which rule caught it and why.

#### Option B — One curl command

If you prefer the terminal, this single command validates the same record via the API:

```bash
curl -s -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{"contract": "customer", "version": "1.0", "record": {"id": "cust-001", "name": "Joe Bloggs", "email": "joe@example.com", "age": 32}}'
```

> This assumes `AUTH_MODE=open` (the default for local dev). For `AUTH_MODE=token` deployments, add `-H "Authorization: Bearer <your-token>"` to the request.

The response will tell you whether the record is valid and list any rule violations.

---

## Troubleshooting

Five issues come up most often.

1. **"Docker is not running"** — Open Docker Desktop and wait for the whale icon in your menu bar to stop animating, then retry `docker compose up`.

2. **Port 8000 already in use** — Open the `.env` file in a text editor, change `API_PORT=8000` to `API_PORT=8001`, then run `docker compose down` followed by `docker compose up`.

3. **Port 8501 already in use** — Open the `.env` file, change `WORKBENCH_PORT=8501` to `WORKBENCH_PORT=8502`, then restart as above.

4. **"API not reachable" banner in the workbench** — Run `docker compose logs api` in your terminal to see what went wrong. The most common causes are a port conflict (see above) or Docker not having fully started yet.

5. **Blank workbench or no contracts listed** — Run `docker compose restart workbench`. On the very first start, the workbench may come up before the contracts are fully loaded. A restart fixes this.

6. **Workbench changes not appearing after editing `ui/app.py`** — The UI image is
   baked at build time. After editing any file under `ui/`, rebuild the image before
   restarting: `docker compose build ui && docker compose up -d --no-deps ui`.

---

## What next?

You are up and running. Here are the natural next steps:

- **Browse the industry templates** in the Contracts section to find ones relevant to your domain
- **Read [`docs/naming_conventions.md`](naming_conventions.md)** to learn how to name and organise your own contracts
- **Read [`docs/rules/README.md`](rules/README.md)** for the full rule reference — every rule type explained with examples
- **Using Claude Desktop or Cursor?** Connect them directly to OpenDQV via the MCP server — read [`docs/llm_integration.md`](llm_integration.md) for setup instructions and available agent tools
- **GraphQL API** (`/graphql`) — introspection active; query or mutate contracts.
- **Token roles** — use `POST /api/v1/tokens/generate?username=alice&role=auditor` to create audit-only tokens for compliance reviewers. Roles: `validator` (default), `reader`, `auditor`, `editor`, `approver`, `admin`. See [`docs/production_deployment.md`](production_deployment.md) for role permissions.
- **Context overrides** — validate the same contract differently per source system or tenant: [`docs/contexts.md`](contexts.md)
- **CLI reference** — local validation, contract lifecycle, code generation without a running server: [`docs/cli.md`](cli.md)
- **Observability** — Prometheus metrics, alert rules, trace log, Grafana panels: [`docs/observability.md`](observability.md)
- **LocalValidator** — validate without a running API server (Python-only, zero latency): [`docs/pandas_integration.md`](pandas_integration.md)

If you get stuck at any point, the `docker compose logs` command is your friend — it shows exactly what each service is doing and usually points straight at the problem.
