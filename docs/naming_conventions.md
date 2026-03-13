# Contract Naming Conventions

This document defines the naming conventions for OpenDQV contracts. Following these rules ensures that the filesystem, the schema registry, and the validation engine stay in sync, and that contracts remain unambiguous when multiple brands or teams share the same instance.

---

## 1. The `domain_entity` Snake Case Convention

Every contract filename must exactly match the `name:` field declared inside the YAML. Both use lowercase snake_case.

```
contracts/logistics_shipment.yaml   →   name: logistics_shipment
contracts/hr_employee.yaml          →   name: hr_employee
contracts/financial_trade.yaml      →   name: financial_trade
```

OpenDQV uses the `name:` field as the canonical identifier across the registry, validation API, and workbench UI. A mismatch between the filename and `name:` is treated as a misconfiguration and will surface as a validation error.

---

## 2. Single-Brand vs Multi-Brand Prefix Patterns

### Single-brand deployments

When only one brand owns all contracts in an instance, entity names can stand alone without a prefix:

- `customer.yaml` → `name: customer`
- `order.yaml` → `name: order`
- `product.yaml` → `name: product`

This is appropriate for standalone deployments where there is no risk of collision.

### Multi-brand deployments

When multiple brands share an OpenDQV instance, prefix every brand-owned contract with the brand or domain slug:

- `luminelle_order.yaml` → `name: luminelle_order`
- `luminelle_customer.yaml` → `name: luminelle_customer`
- `meraki_order.yaml` → `name: meraki_order`

### Shared cross-domain contracts

Contracts owned by a central team and consumed by multiple brands use the domain as the prefix:

- `financial_trade.yaml` → `name: financial_trade`
- `logistics_shipment.yaml` → `name: logistics_shipment`
- `hr_employee.yaml` → `name: hr_employee`

The domain prefix signals that the contract is infrastructure-level, not brand-specific.

---

## 3. Domain Tagging

OpenDQV does not enforce tags, but adding a `tags:` field to the contract header is strongly recommended. Tags drive search and filtering in the workbench and make contracts discoverable across teams.

```yaml
name: luminelle_order
version: "1.0.0"
description: Order records for Luminelle Commerce
tags: [retail, e-commerce, luminelle]
fields:
  ...
```

Suggested tag categories:

| Category | Example values |
|----------|---------------|
| Industry | `retail`, `finance`, `logistics`, `hr` |
| Brand/tenant | `luminelle`, `meraki` |
| Scope | `shared`, `brand-specific`, `experimental` |

---

## 4. Collision-Avoidance Rules for Shared Tenants

When multiple brands or teams share a single OpenDQV instance, follow these rules to prevent naming collisions:

1. **Never use bare generic names** (`order`, `product`, `customer`) if more than one brand is active on the instance. A bare `order` contract is ambiguous and will overwrite or shadow another brand's contract of the same name in the registry.

2. **Brand-owned contracts** must use `<brand>_<entity>` — for example, `luminelle_order`, `luminelle_customer`, `luminelle_returns`.

3. **Shared infrastructure contracts** must use `<domain>_<entity>` — for example, `financial_trade`, `logistics_shipment`, `hr_employee`.

4. **Avoid ambiguous abbreviations.** If a full domain name is `financial`, do not abbreviate the contract as `fin_trade`. Abbreviations create collisions when another team introduces a similarly abbreviated prefix. Use the full form: `financial_trade`.

5. **Keep slugs stable.** Once a contract is registered and consumed by pipelines, renaming it is a breaking change. Choose the prefix carefully before the first release.

---

## 5. Real-World Example: Luminelle Commerce

The table below shows how Luminelle Commerce contracts coexist with shared infrastructure contracts on the same instance.

| Contract filename | `name:` field | Owner | Scope |
|-------------------|---------------|-------|-------|
| `luminelle_order.yaml` | `luminelle_order` | Luminelle Commerce | Brand-specific |
| `luminelle_customer.yaml` | `luminelle_customer` | Luminelle Commerce | Brand-specific |
| `luminelle_returns.yaml` | `luminelle_returns` | Luminelle Commerce | Brand-specific |
| `financial_trade.yaml` | `financial_trade` | Finance Team | Shared |
| `logistics_shipment.yaml` | `logistics_shipment` | Logistics Team | Shared |
| `hr_employee.yaml` | `hr_employee` | HR Team | Shared |

All six contracts can live in the same `contracts/` directory and registry without collision because each name is fully qualified by either a brand slug or a domain slug.

---

## 6. Quick Reference: Naming a New Contract

Before creating a contract file, work through this checklist:

- [ ] Is this a single-brand or multi-brand deployment? If multi-brand, a prefix is required.
- [ ] Is this contract brand-specific or shared infrastructure? Choose `<brand>_` or `<domain>_` accordingly.
- [ ] Is the chosen name unique in the current `contracts/` directory?
- [ ] Does the filename (without `.yaml`) exactly match the `name:` field you intend to use inside the YAML?
- [ ] Have you avoided abbreviations that could clash with another team's prefix?
- [ ] Have you added `tags:` metadata to aid discovery in the workbench?
- [ ] If this contract replaces or extends an existing one, have you incremented `version:` and noted the change?

---

## 7. See Also

- `docs/quickstart.md` — getting started with your first contract
- `docs/rules/README.md` — rule authoring reference and rule type catalogue
