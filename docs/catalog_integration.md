# Data Catalog Integration — Index

> This page is a redirect index. Each catalog has its own dedicated integration guide below.

OpenDQV exposes contract metadata via `GET /api/v1/registry` and `GET /api/v1/contracts/{name}`.
The optional `asset_id` field links any contract to a catalog asset using that catalog's native URN
convention — see [`docs/index.md`](index.md) for the cross-catalog URN table.

## Catalog integration guides

| Catalog | Guide |
|---------|-------|
| DataHub | [`datahub_integration.md`](datahub_integration.md) |
| Atlan | [`atlan_integration.md`](atlan_integration.md) |
| Collibra | [`collibra_integration.md`](collibra_integration.md) |
| OpenMetadata | [`openmetadata_integration.md`](openmetadata_integration.md) |

## Related

- [`asset_id_uri_convention.md`](asset_id_uri_convention.md) — URN naming rules
- [`connector_sdk_spec.md`](connector_sdk_spec.md) — connector interface for scheduled syncs
