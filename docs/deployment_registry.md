# OpenDQV — Deployment Concentration Risk Registry

**DORA Article 28 / FCA PS21/3 Operational Resilience Compliance Reference**

Last updated: 2026-03-09 | Version 1.0 | Owner: Platform Engineering

---

## Purpose

This registry supports ICT concentration risk assessment under DORA Article 28.

Deploying organisations are invited to anonymously register their deployment so that:
1. We can assess the **concentration of critical financial infrastructure** dependent on OpenDQV
2. We can notify known deployers promptly when a P0/P1 security advisory is issued
3. Deployers can demonstrate due diligence to their regulators (FCA, EBA, ECB)

**Registration is voluntary and entirely anonymous.** No organisation names, legal entities, or
personnel details are collected. Entries use self-assigned anonymous identifiers.

---

## How to Register

Open a **private** GitHub Security Advisory draft (not a public issue) with:

- Subject: `[DEPLOYMENT-REGISTRY] <your-anonymous-id>`
- Body: Fill in the template below

You will receive a confirmation within 48 hours. Your entry is added to this registry in
anonymised form only.

### Registration template

```
Anonymous ID: <e.g. ORG-EU-BANKING-001 — self-assigned, not your real name>
Region: <EU / UK / APAC / Americas / Other>
Sector: <Banking / Insurance / FinTech / Healthcare / Other>
Deployment type: <Self-hosted / Air-gapped / Cloud-hosted (self-managed)>
Approximate scale: <Small (<10k validations/day) / Medium (10k–1M/day) / Large (>1M/day)>
DORA/FCA in scope: <Yes / No / Unknown>
Notification preference: <opendqv@bgmsconsultants.com / GitHub Advisory watch / None>
```

---

## Known Deployments (Anonymised)

The table below represents self-registered entries. It is maintained on a best-effort basis and
is not an exhaustive count of all deployments.

| Anonymous ID | Region | Sector | Scale | DORA/FCA Scope | Registered |
|---|---|---|---|---|---|
| *(No entries yet — be the first to register)* | | | | | |

---

## Concentration Risk Assessment

### Current assessment (as of 2026-03-09)

**Risk level: LOW — no concentration risk at this time.**

Rationale:
- No known production deployments in critical financial infrastructure (self-reported)
- Apache 2.0 licence — organisations can self-maintain; no single vendor dependency
- No SaaS / cloud-hosted model — each deployment is independently controlled
- Switching cost assessed as LOW (Apache 2.0 licence; no proprietary data formats; standard REST API)

### Triggers for escalation

The concentration risk assessment would be escalated if:

| Trigger | Threshold | Action |
|---------|-----------|--------|
| Registered deployments in critical FI | ≥ 3 DORA-scope deployments | Notify EBA/FCA; engage CREST pentest |
| Single-region concentration | ≥ 5 deployments in one jurisdiction | Add regional redundancy guidance |
| Scale concentration | Any single deployment > 10M validations/day | Direct engagement with deploying org |

---

## Regulatory Context

### DORA Article 28 — Concentration Risk

DORA Article 28(2) requires financial entities to identify and monitor concentration risk arising
from dependencies on ICT third-party service providers. This registry provides:

- A publicly disclosed mechanism for deploying organisations to self-register
- An anonymised aggregate view of the deployment landscape
- Evidence that the OpenDQV project actively monitors concentration risk

### FCA PS21/3 — Important Business Services

For UK-regulated firms, this registry supports evidence requirements for Important Business
Services mapping. Firms should document OpenDQV as a third-party ICT dependency and assess
whether it supports an Important Business Service.

### BCBS 239 — Risk Data Aggregation

For banks subject to BCBS 239, the validation contracts and TRACE_LOG outputs produced by
OpenDQV are risk data aggregation artefacts. This registry helps demonstrate that the
underlying platform has disclosed its operational risk profile.

---

## Notification Process

When a P0 or P1 security advisory is issued, OpenDQV will:

1. Publish the GitHub Security Advisory (public, triggering GitHub's security feed)
2. Email all registered deployers at their stated notification address within **2 hours** of advisory publication
3. Include: CVE ID, affected versions, workarounds, patch version

Deployers who have not registered can still subscribe to GitHub Security Advisories by
watching the repository with "Security alerts" enabled.

---

## Annual Review

This registry is reviewed annually (or after any P0/P1 incident) to:

- Verify the concentration risk assessment remains current
- Update the regulatory context for new or amended regulations
- Engage with registered deployers for feedback on the project's operational risk profile

Next scheduled review: **2027-03-09**

---

## Contact

- Deployment registry registration: `opendqv@bgmsconsultants.com` (subject: `[DEPLOYMENT-REGISTRY]`)
- General contact: `https://github.com/OpenDQV/OpenDQV/issues`
- Vulnerability response: `docs/security/vulnerability_response_playbook.md`
