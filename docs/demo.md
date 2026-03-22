# OpenDQV Demo Environment

A pre-seeded Docker environment for evaluating OpenDQV without any manual setup.

One command. Under 2 minutes. Real data. Full governance UI.

---

## Launch

```bash
# Clone (if you haven't already)
git clone https://github.com/OpenDQV/OpenDQV.git
cd OpenDQV

# Copy the environment file (required for Docker Compose)
cp .env.example .env

# Launch the demo environment
docker compose -f docker-compose.demo.yml up -d
```

The demo uses ports **8080** (API) and **8502** (UI) to avoid conflicts with the standard `docker compose up -d` on 8000/8501.

Once the seeder exits (watch with `docker compose -f docker-compose.demo.yml logs -f demo-seeder`), everything is ready.

---

## What's pre-loaded

The seeder loads ~740 validation events across 7 contracts:

| Contract | Records | Approx. pass rate | Domain |
|----------|---------|------------------|--------|
| `customer` | 200 | 85% | CRM / customer data |
| `proof_of_play` | 150 | 80% | Out-of-home advertising |
| `sf_contact` | 100 | 90% | Salesforce CRM |
| `banking_transaction` | 100 | 88% | Payments / finance |
| `logistics_shipment` | 80 | 92% | Supply chain |
| `healthcare_patient` | 60 | 85% | Clinical / NHS |
| `demo_order` | 50 | 83% | Lifecycle demo contract |

The `demo_order` contract also goes through a complete **draft → review → active** lifecycle so you can see the governance audit trail in action from day one.

---

## Endpoints

| Service | URL |
|---------|-----|
| API | http://localhost:8080 |
| Swagger UI | http://localhost:8080/docs |
| GraphQL playground | http://localhost:8080/graphql |
| Streamlit workbench | http://localhost:8502 |

---

## Suggested 5-step exploration

**Step 1 — Check validation statistics (Postman or curl)**

```bash
curl http://localhost:8080/api/v1/stats
```

You should see ~740 total validations with per-contract breakdowns.

**Step 2 — Open the Streamlit workbench**

Go to http://localhost:8502 and click the **Monitoring** tab. You'll see live pass/fail charts, top failing contracts, and a trend view — all populated with the seeded data.

**Step 3 — Validate a record**

```bash
# This will fail with per-field error messages
curl -X POST http://localhost:8080/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{"contract": "customer", "record": {"email": "not-valid", "age": 999}}'
```

**Step 4 — Browse the demo_order contract lifecycle**

In the Streamlit UI → **Contracts** tab → select `demo_order`. You'll see:
- Status: ACTIVE
- Version history showing draft → review → active transitions
- Hash-chained audit trail

**Step 5 — Explore the API in Postman**

Import `postman/OpenDQV.postman_collection.json` and `postman/OpenDQV.postman_environment.json`. Change `base_url` to `http://localhost:8080`. Run the **Invalid customer record — the aha moment** request to see per-field validation errors in one call.

---

## Resetting the demo

To wipe all data and start fresh:

```bash
docker compose -f docker-compose.demo.yml down -v
docker compose -f docker-compose.demo.yml up -d
```

The `-v` flag removes the named volume (`db-data`). The seeder will re-run and re-seed from scratch.

---

## About AUTH_MODE=open

The demo runs with `AUTH_MODE=open`. This means:

- No token is required to call any endpoint
- All callers have admin-level access
- This is **intentional for evaluation** — you can explore every API feature without token management

When you're ready for production, see [docs/production_deployment.md](production_deployment.md) and [SECURITY.md](../SECURITY.md) for `AUTH_MODE=token` setup with proper PAT management.

---

## Moving to production

The demo environment is not suitable for production use. Before going live:

1. Generate a real `SECRET_KEY`: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Set `AUTH_MODE=token` in your `.env`
3. Use `docker compose up -d` (standard compose) or `docker compose -f docker-compose.prod.yml up -d`
4. See [docs/production_deployment.md](production_deployment.md) for the full checklist

The demo contracts (`demo_order`) and seeded validation data exist only in the demo volume — they don't affect your production contracts directory.
