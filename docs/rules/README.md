# OpenDQV Rule Types — Documentation Index

## Core rules
- [not_empty](../../README.md#rule-types#not_empty) — field must be present and non-empty
- [regex](../../README.md#rule-types#regex) — field must match a pattern (supports `negate: true` and `builtin:` shorthands)
- [range](../../README.md#rule-types#range) — numeric field within min/max bounds
- [min / max](../../README.md#rule-types#min-max) — single-sided numeric bounds
- [min_length / max_length](../../README.md#rule-types#string-length) — string length constraints
- [date_format](../../README.md#rule-types#date_format) — field must be a parseable date
- [unique](../../README.md#rule-types#unique) — field must be unique (supports `group_by`)
- [lookup](../../README.md#rule-types#lookup) — field value must appear in a reference list (supports `all_of`)
- [compare](../../README.md#rule-types#compare) — field compared to another field or sentinel (`today`, `now`)
- [required_if](../../README.md#rule-types#required_if) — field required when another field equals a value
- [min_age / max_age](../../README.md#rule-types#age) — date field implies an age constraint

- [age_match](age_match.md) — declared age must be consistent with date of birth

## New rule types (v1.0.0)
- [checksum](checksum.md) — validates identifier check digits (IBAN, GTIN, NHS, ISIN, LEI, VIN, ISRC, CPF)
- [cross_field_range](cross_field_range.md) — value must be between two other fields
- [field_sum](field_sum.md) — sum of named fields must equal a target value
- [forbidden_if](forbidden_if.md) — field must be absent when condition is met
- [conditional_value](../../README.md#rule-types#conditional_value) — field must equal specific value when condition is met
- [date_diff](date_diff.md) — difference between two date fields within a range
- [ratio_check](ratio_check.md) — ratio of two fields within a range
- [conditional_lookup](../../README.md#rule-types#conditional_lookup) — lookup list conditioned on another field
- [geospatial_bounds](geospatial_bounds.md) — lat/lon pair within geographic bounding box

## Feature flags on existing rules
- [compare_to: today/now](compare_to_today.md) — compare against current date/time
- [negate: true on regex](builtin_patterns.md) — field must NOT match pattern
- [builtin: pattern shorthands](builtin_patterns.md) — 11 built-in validated patterns
- [group_by on unique](../../README.md#rule-types#grouped-unique) — uniqueness within groups
- [all_of on lookup](../../README.md#rule-types#all-of-lookup) — validate each element in a list field
- [algorithm: semver on compare](../../README.md#rule-types#semver-compare) — semantic version comparison

## Contract features (v1.0.0)
- [sensitive_fields](sensitive_fields.md) — privacy-safe field suppression from logs and responses
- [REVIEW lifecycle](review_lifecycle.md) — maker-checker approval workflow
- [/explain endpoint](explain_endpoint.md) — plain-English contract descriptions for compliance officers
- [validate_in_states](../../README.md#rule-types#validate-in-states) — restrict validation to specific contract statuses
- [federation_tier](../core/federation.md#federation-tier) — REGULATORY/COMMERCIAL/COMMUNITY classification

## Patterns
- [distribution_check](../patterns/distribution_check.md) — out of scope; use OpenDQV + Evidently
- [Regulatory Federation Pattern](../patterns/federation_deprecation.md) — governance hierarchy model
