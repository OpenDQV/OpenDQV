# OpenDQV ROI Calculator

Use this worksheet to estimate the return on investment from deploying OpenDQV at your organisation. All figures are illustrative starting points — replace them with your actuals.

---

## Part 1: Cost of Poor Data Quality (Before OpenDQV)

### 1.1 Rework and correction labour

| Item | Your value |
|------|-----------|
| Engineers/analysts spending time fixing bad data (FTE) | ___ |
| Average fully-loaded cost per FTE per year (£/$/€) | ___ |
| % of time spent on data quality issues | ___ % |
| **Annual labour cost of DQ rework** | = FTE × cost × % |

### 1.2 Failed pipeline / job reruns

| Item | Your value |
|------|-----------|
| Pipeline failures per month attributed to bad data | ___ |
| Average engineer-hours to diagnose and rerun | ___ |
| Engineer hourly cost | ___ |
| **Annual pipeline failure cost** | = failures × 12 × hours × cost |

### 1.3 Downstream system errors and incidents

| Item | Your value |
|------|-----------|
| Incidents per year caused by bad data reaching downstream systems | ___ |
| Average cost per incident (P1/P2 engineer time + customer impact) | ___ |
| **Annual incident cost** | = incidents × cost |

### 1.4 Regulatory fines and compliance risk

| Item | Your value |
|------|-----------|
| Regulatory submissions rejected or amended due to data quality | ___ per year |
| Average cost per rejection (resubmission + legal + fines) | ___ |
| **Annual compliance cost** | = rejections × cost |

### 1.5 Missed revenue / churn

| Item | Your value |
|------|-----------|
| Customer records with bad contact data (email/phone) | ___ % |
| Revenue per customer contact reached | ___ |
| Customers affected per year | ___ |
| **Annual missed revenue** | = % × revenue × customers |

---

**Total annual cost of poor data quality (A):**
```
A = 1.1 + 1.2 + 1.3 + 1.4 + 1.5
```

---

## Part 2: OpenDQV Deployment Cost (After)

### 2.1 Initial setup

| Item | Your estimate |
|------|--------------|
| Engineer days to deploy and configure (self-hosted) | ___ days |
| Engineer daily cost | ___ |
| Initial contract authoring (days per contract × number of contracts) | ___ |
| **Total setup cost** | one-time |

**Indicative benchmarks:**
- Simple deployment (single service, 3–5 contracts): 1–3 engineer-days
- Multi-service with context overrides: 3–10 engineer-days
- Enterprise with federation + audit trail: 2–4 weeks

### 2.2 Ongoing maintenance

| Item | Your estimate |
|------|--------------|
| Contract reviews and updates per month (hours) | ___ |
| Infrastructure cost per month (server/cloud) | ___ |
| **Annual ongoing cost** | = (hours × hourly_rate × 12) + (infra × 12) |

---

**Total OpenDQV cost (B):**
```
B = 2.1 + 2.2 (year 1)
```

---

## Part 3: ROI Calculation

### Reduction factors (illustrative reduction estimates)

| Problem area | Typical reduction | Your reduction |
|---|---|---|
| Rework labour | 60–80% | ___ % |
| Pipeline failures | 70–90% | ___ % |
| Downstream incidents | 50–75% | ___ % |
| Regulatory issues | 80–95% | ___ % |
| Missed revenue (bad contacts) | 40–60% | ___ % |

### Year 1 ROI

```
Savings = A × weighted_reduction_factor
Net benefit = Savings − B
ROI % = (Net benefit / B) × 100
Payback period = B / (Savings / 12) months
```

---

## Part 4: Beyond Financial ROI

Consider non-financial benefits that are harder to quantify but often exceed the direct financial return:

- **Regulatory trust:** A complete audit trail (engine_version, contract_version, record_id) is required for EMA, MiFIR, Basel III, and SOX submissions. The cost of a failed regulatory audit dwarfs OpenDQV's deployment cost.
- **Engineering morale:** Data quality incidents are demotivating. Teams freed from fire-fighting report significantly higher satisfaction.
- **Data product trustworthiness:** Self-service analytics only works when consumers trust the data. OpenDQV provides a verifiable quality signal that enables data mesh adoption.
- **Vendor negotiation strength:** Proving data quality at source gives you contractual advantage with data vendors and SaaS providers.

---

> All figures in this section are illustrative. OpenDQV has not been deployed in production by any organisation.

## Worked Example

A hypothetical mid-size engineering team with 50 engineers:
- 8 FTE × £80K × 25% on DQ = **£160K/year rework**
- 40 pipeline failures/month × 2hrs × £60/hr × 12 = **£57,600/year**
- 5 P2 incidents/year × £25K = **£125K/year incidents**
- 2 regulatory rejections × £80K = **£160K/year compliance**

**Total A = £502,600/year**

OpenDQV deployment: 10 days setup (£6K) + £15K/year maintenance = **B = £21K year 1**

At 70% average reduction:
- **Savings = £351,820**
- **Net benefit = £330,820**
- **ROI = 1,575%**
- **Payback = 0.7 months**
