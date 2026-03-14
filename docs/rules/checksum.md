# checksum rule

**Rule type:** `checksum`
**Released:** v1.0.0

## Overview

The `checksum` rule validates that an identifier's check digit(s) are mathematically correct according to the standard algorithm for that identifier type.

Supported algorithms:

| Algorithm | Identifier | Industry |
|-----------|-----------|----------|
| `mod10_gs1` | GTIN-8, GTIN-12, GTIN-13, GTIN-14, GLN, SSCC | Retail, FMCG, Logistics, Agriculture |
| `iban_mod97` | IBAN (ISO 13616) | Banking, Financial Services |
| `isin_mod11` | ISIN | Financial Services, Insurance |
| `lei_mod97` | LEI (20-character) | Financial Services, Banking |
| `nhs_mod11` | NHS Number (10-digit) | Healthcare |
| `cpf_mod11` | Brazilian CPF (11-digit) | Financial Services (Brazil) |
| `vin_mod11` | Vehicle Identification Number (17-character) | Automotive, Insurance |
| `isrc_luhn` | ISRC (International Standard Recording Code) | Media & Entertainment |

> **Important:** Checksums validate that the check digit(s) are mathematically correct — NOT that the identifier is registered, active, or assigned. Combine with a `regex` rule for format validation and a `lookup` rule if you need to verify registration.

## Syntax

```yaml
- name: validate_iban_checksum
  type: checksum
  field: iban
  checksum_algorithm: iban_mod97
  error_message: "IBAN has invalid check digits"
  severity: error
```

## Algorithms

### mod10_gs1

**Standard:** GS1 Mod-10 (Luhn variant defined by GS1)
**Identifiers:** GTIN-8, GTIN-12, GTIN-13, GTIN-14, GLN, SSCC
**Industries:** Retail, FMCG, Logistics, Agriculture

GS1's check digit algorithm weights alternating digits by 3 and 1, sums them, and checks that the total mod 10 equals zero.

```yaml
- name: gtin_checksum_valid
  type: checksum
  field: gtin
  checksum_algorithm: mod10_gs1
  error_message: "GTIN check digit is invalid"
  severity: error
```

Combine with a `regex` rule to enforce the correct digit count:

```yaml
- name: gtin13_format
  type: regex
  field: gtin
  pattern: "^\\d{13}$"
  error_message: "GTIN-13 must be exactly 13 digits"
  severity: error

- name: gtin13_checksum
  type: checksum
  field: gtin
  checksum_algorithm: mod10_gs1
  error_message: "GTIN-13 check digit is invalid"
  severity: error
```

### iban_mod97

**Standard:** ISO 13616
**Industries:** Banking, Financial Services

IBAN validation: move the first 4 characters to the end, replace letters with their numeric equivalents (A=10, B=11, ...), and verify the resulting integer mod 97 equals 1.

```yaml
- name: iban_checksum_valid
  type: checksum
  field: iban
  checksum_algorithm: iban_mod97
  error_message: "IBAN has invalid check digits"
  severity: error
```

Example valid IBAN format rule to pair with this:

```yaml
- name: iban_format
  type: regex
  field: iban
  pattern: "^[A-Z]{2}\\d{2}[A-Z0-9]{1,30}$"
  error_message: "IBAN format is invalid"
  severity: error
```

### isin_mod11

**Standard:** ISO 6166
**Industries:** Financial Services, Insurance

ISIN validation: convert all characters to digits (A=10, ..., Z=35), apply Luhn mod-10 on the resulting digit string, then verify the check digit.

```yaml
- name: isin_checksum_valid
  type: checksum
  field: isin
  checksum_algorithm: isin_mod11
  error_message: "ISIN check digit is invalid"
  severity: error
```

Pair with format rule:

```yaml
- name: isin_format
  type: regex
  field: isin
  pattern: "^[A-Z]{2}[A-Z0-9]{9}\\d$"
  error_message: "ISIN must be 12 characters: 2 letter country code, 9 alphanumeric, 1 digit check"
  severity: error
```

### lei_mod97

**Standard:** ISO 17442 (Global LEI System)
**Industries:** Financial Services, Banking

LEI is a 20-character identifier. The last two characters are check digits computed using the same ISO 7064 Mod 97-10 algorithm as IBAN.

```yaml
- name: lei_checksum_valid
  type: checksum
  field: lei_code
  checksum_algorithm: lei_mod97
  error_message: "LEI check digits are invalid"
  severity: error
```

Pair with format rule:

```yaml
- name: lei_format
  type: regex
  field: lei_code
  pattern: "^[A-Z0-9]{18}\\d{2}$"
  error_message: "LEI must be 20 alphanumeric characters ending in 2 digits"
  severity: error
```

### nhs_mod11

**Standard:** NHS Connecting for Health algorithm
**Industries:** Healthcare (UK)

NHS numbers are 10 digits. The check digit (position 10) is computed using a weighted Mod 11 algorithm. A result of 11 indicates a check digit of 0; a result of 10 means the number is invalid.

```yaml
- name: nhs_number_checksum
  type: checksum
  field: nhs_number
  checksum_algorithm: nhs_mod11
  error_message: "NHS number has an invalid check digit"
  severity: error
```

Pair with format rule:

```yaml
- name: nhs_number_format
  type: regex
  field: nhs_number
  pattern: "^\\d{10}$"
  error_message: "NHS number must be exactly 10 digits"
  severity: error
```

### cpf_mod11

**Standard:** Brazilian Receita Federal CPF algorithm
**Industries:** Financial Services (Brazil)

CPF is an 11-digit Brazilian taxpayer identifier. The 10th and 11th digits are check digits computed using two separate Mod 11 passes with weights 10..2 and 11..2 respectively.

```yaml
- name: cpf_checksum_valid
  type: checksum
  field: cpf_number
  checksum_algorithm: cpf_mod11
  error_message: "CPF check digits are invalid"
  severity: error
```

Pair with format rule (digits only, no punctuation):

```yaml
- name: cpf_format
  type: regex
  field: cpf_number
  pattern: "^\\d{11}$"
  error_message: "CPF must be exactly 11 digits (no punctuation)"
  severity: error
```

### vin_mod11

**Standard:** ISO 3779 (North American VIN check digit, position 9)
**Industries:** Automotive, Insurance

VIN is 17 characters. Position 9 (0-indexed: position 8) is the check digit, computed by transliterating each character to a numeric value, multiplying by a positional weight, summing, and taking mod 11. The check digit may be a digit 0-9 or the letter X (representing 10).

```yaml
- name: vin_checksum_valid
  type: checksum
  field: vin
  checksum_algorithm: vin_mod11
  error_message: "VIN check digit (position 9) is invalid"
  severity: error
```

Pair with format rule:

```yaml
- name: vin_format
  type: regex
  field: vin
  pattern: "^[A-HJ-NPR-Z0-9]{17}$"
  error_message: "VIN must be 17 characters (letters excluding I, O, Q; and digits)"
  severity: error
```

> Note: VIN check digit validation applies to vehicles manufactured for the North American market. European VINs use the same 17-character format but do not mandate a check digit at position 9.

### isrc_luhn

**Standard:** ISO 3901 (ISRC format validation with Luhn check)
**Industries:** Media & Entertainment

ISRC (International Standard Recording Code) is a 12-character identifier: 2-letter country code, 3-character registrant code, 2-digit year, 5-digit designation code. The `isrc_luhn` algorithm validates the structural integrity of the ISRC using a Luhn-style check on the numeric components.

```yaml
- name: isrc_checksum_valid
  type: checksum
  field: isrc
  checksum_algorithm: isrc_luhn
  error_message: "ISRC identifier is structurally invalid"
  severity: error
```

Pair with format rule:

```yaml
- name: isrc_format
  type: regex
  field: isrc
  pattern: "^[A-Z]{2}-?[A-Z0-9]{3}-?\\d{2}-?\\d{5}$"
  error_message: "ISRC must match format: CC-XXX-YY-NNNNN"
  severity: error
```

## Using checksum with condition

Apply checksum validation only when the identifier type is present:

```yaml
- name: iban_only_if_sepa
  type: checksum
  field: bank_account_number
  checksum_algorithm: iban_mod97
  condition:
    field: payment_method
    value: SEPA
  error_message: "IBAN check digits are invalid for SEPA payment"
  severity: error
```

## Industry contract examples

### Banking contract

```yaml
contract:
  name: payment_instruction
  version: "1.0"
  rules:
    - name: iban_format
      type: regex
      field: creditor_iban
      pattern: "^[A-Z]{2}\\d{2}[A-Z0-9]{1,30}$"
      error_message: "Creditor IBAN format is invalid"
      severity: error

    - name: iban_checksum
      type: checksum
      field: creditor_iban
      checksum_algorithm: iban_mod97
      error_message: "Creditor IBAN has invalid check digits"
      severity: error

    - name: lei_checksum
      type: checksum
      field: originator_lei
      checksum_algorithm: lei_mod97
      error_message: "Originator LEI check digits are invalid"
      severity: error
```

### Healthcare contract

```yaml
contract:
  name: patient_record
  version: "1.0"
  rules:
    - name: nhs_format
      type: regex
      field: nhs_number
      pattern: "^\\d{10}$"
      error_message: "NHS number must be 10 digits"
      severity: error

    - name: nhs_checksum
      type: checksum
      field: nhs_number
      checksum_algorithm: nhs_mod11
      error_message: "NHS number check digit is invalid"
      severity: error
```

### Retail/FMCG contract

```yaml
contract:
  name: product_master
  version: "1.0"
  rules:
    - name: gtin_format
      type: regex
      field: gtin
      pattern: "^\\d{8}$|^\\d{12}$|^\\d{13}$|^\\d{14}$"
      error_message: "GTIN must be 8, 12, 13, or 14 digits"
      severity: error

    - name: gtin_checksum
      type: checksum
      field: gtin
      checksum_algorithm: mod10_gs1
      error_message: "GTIN check digit is invalid"
      severity: error
```

### Automotive/Insurance contract

```yaml
contract:
  name: vehicle_record
  version: "1.0"
  rules:
    - name: vin_format
      type: regex
      field: vin
      pattern: "^[A-HJ-NPR-Z0-9]{17}$"
      error_message: "VIN format is invalid"
      severity: error

    - name: vin_checksum
      type: checksum
      field: vin
      checksum_algorithm: vin_mod11
      error_message: "VIN check digit is invalid"
      severity: error
```

## See also

- `regex` rule — pair with checksum for format validation before check digit computation
- `lookup` rule — verify the identifier is registered in a reference system (separate from checksum validity)
- `condition` block — apply checksum rules conditionally based on identifier type
