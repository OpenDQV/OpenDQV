# OpenDQV Rule Types — Documentation Index

## Core rules
- [not_empty](core_rules.md#1-not_empty) — field must be present and non-empty
- [regex](core_rules.md#2-regex) — field must match a pattern (supports `negate: true` and `builtin:` shorthands)
- [min](core_rules.md#3-min) — numeric field must be >= minimum
- [max](core_rules.md#4-max) — numeric field must be <= maximum
- [range](core_rules.md#5-range) — numeric field within min/max bounds
- [min_length](core_rules.md#6-min_length) — string length minimum
- [max_length](core_rules.md#7-max_length) — string length maximum
- [date_format](core_rules.md#8-date_format) — field must be a parseable date
- [allowed_values](core_rules.md#9-allowed_values) — field value must be one of an inline list
- [lookup](core_rules.md#10-lookup) — field value must appear in a reference list (supports `all_of`)
- [compare](core_rules.md#11-compare) — field compared to another field or sentinel (`today`, `now`)
- [required_if](core_rules.md#12-required_if) — field required when another field equals a value
- [unique](core_rules.md#13-unique) — field must be unique (supports `group_by`)
- [Common Pitfalls](core_rules.md#common-pitfalls) — `min:` vs `min_length:` and other traps
- min_age / max_age — date field implies an age constraint (see Rule model)

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
