# OpenDQV Rule Types — Documentation Index

## Core rules
- [not_empty](../../README.md#rules) — field must be present and non-empty
- [regex](../../README.md#rules) — field must match a pattern (supports `negate: true` and `builtin:` shorthands)
- [range](../../README.md#rules) — numeric field within min/max bounds
- [min / max](../../README.md#rules) — single-sided numeric bounds
- [min_length / max_length](../../README.md#rules) — string length constraints
- [date_format](../../README.md#rules) — field must be a parseable date
- [unique](../../README.md#rules) — field must be unique (supports `group_by`)
- [lookup](../../README.md#rules) — field value must appear in a reference list (supports `all_of`)
- [allowed_values](../../README.md#rules) — field value must be one of an inline list (no external file needed)
- [compare](../../README.md#rules) — field compared to another field or sentinel (`today`, `now`)
- [required_if](../../README.md#rules) — field required when another field equals a value
- [min_age / max_age](../../README.md#rules) — date field implies an age constraint

- [age_match](age_match.md) — declared age must be consistent with date of birth

## New rule types (v1.0.0)
- [checksum](checksum.md) — validates identifier check digits (IBAN, GTIN, NHS, ISIN, LEI, VIN, ISRC, CPF)
- [cross_field_range](cross_field_range.md) — value must be between two other fields
- [field_sum](field_sum.md) — sum of named fields must equal a target value
- [forbidden_if](forbidden_if.md) — field must be absent when condition is met
- [conditional_value](../../README.md#rules) — field must equal specific value when condition is met
- [date_diff](date_diff.md) — difference between two date fields within a range
- [ratio_check](ratio_check.md) — ratio of two fields within a range
- [conditional_lookup](../../README.md#rules) — lookup list conditioned on another field
- [geospatial_bounds](geospatial_bounds.md) — lat/lon pair within geographic bounding box

## Feature flags on existing rules
- [compare_to: today/now](compare_to_today.md) — compare against current date/time
- [negate: true on regex](builtin_patterns.md) — field must NOT match pattern
- [builtin: pattern shorthands](builtin_patterns.md) — 11 built-in validated patterns
- [group_by on unique](../../README.md#rules) — uniqueness within groups
- [all_of on lookup](../../README.md#rules) — validate each element in a list field
- [algorithm: semver on compare](../../README.md#rules) — semantic version comparison

## Contract features (v1.0.0)
- [sensitive_fields](sensitive_fields.md) — privacy-safe field suppression from logs and responses
- [REVIEW lifecycle](review_lifecycle.md) — maker-checker approval workflow
- [/explain endpoint](explain_endpoint.md) — plain-English contract descriptions for compliance officers
- [validate_in_states](../../README.md#rules) — restrict validation to specific contract statuses
- [federation_tier](../core/federation.md#federation-tier) — REGULATORY/COMMERCIAL/COMMUNITY classification

## Patterns
- [distribution_check](../patterns/distribution_check.md) — out of scope; use OpenDQV + Evidently
- [Regulatory Federation Pattern](../patterns/federation_deprecation.md) — governance hierarchy model
