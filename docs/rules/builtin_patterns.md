# Built-in Pattern Shorthands

Use `pattern: builtin:<name>` in any `regex` rule instead of writing the regular expression yourself. Built-in patterns are maintained by OpenDQV and kept in sync with the relevant specification.

## Available patterns

| Shorthand | Pattern validates | Example valid value |
|---|---|---|
| `builtin:semver` | Semantic version (SemVer 2.0) | `1.2.3`, `2.0.0-beta.1` |
| `builtin:ipv4` | IPv4 address | `192.168.1.1`, `10.0.0.1` |
| `builtin:ipv6` | IPv6 address (full form) | `2001:0db8:0000:0000:0000:0000:0000:0001` |
| `builtin:cve_id` | CVE identifier | `CVE-2023-44228` |
| `builtin:smpte-timecode` | SMPTE timecode | `01:00:00:00`, `23:59:59;29` |
| `builtin:did` | Decentralized Identifier | `did:web:example.com` |
| `builtin:ean13` | EAN-13 barcode format | `5901234123457` |
| `builtin:isbn13` | ISBN-13 format | `9780306406157` |
| `builtin:uuid` | UUID (lowercase hex) | `550e8400-e29b-41d4-a716-446655440000` |
| `builtin:email` | Email address | `user@example.com` |
| `builtin:url` | HTTP/HTTPS URL | `https://example.com/path` |

## Example YAML

```yaml
- name: sdk_version_format
  type: regex
  field: sdk_version
  pattern: builtin:semver
  error_message: "SDK version must follow Semantic Versioning (e.g., 1.2.3)"

- name: server_ip
  type: regex
  field: server_ip
  pattern: builtin:ipv4
  error_message: "Server IP must be a valid IPv4 address"
```

## Format validation only

Built-in patterns validate **format only**, not existence or semantic validity. Examples:

- `builtin:ean13` checks that the value is 13 digits but does not verify the GS1 check digit. Use `type: checksum` with `checksum_algorithm: mod10_gs1` for full validation.
- `builtin:email` checks structural format but does not verify the domain exists or the mailbox is reachable.
- `builtin:url` checks that the URL begins with `http://` or `https://` and has a valid structure but does not perform a live reachability check.

## Combining with `negate: true`

Any built-in pattern can be negated to assert the field does **not** match the pattern:

```yaml
- name: not_a_test_ip
  type: regex
  field: source_ip
  pattern: builtin:ipv4
  negate: true
  error_message: "Source IP must not be a valid IPv4 address (this field expects hostnames)"
```

## See also

- [checksum](checksum.md) — validates identifier check digits (IBAN, GTIN, NHS, ISIN, LEI, VIN, ISRC, CPF)
- [regex](../core/rules.md#regex) — full regex rule documentation including `negate`
