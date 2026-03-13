# OpenDQV Community Translation Programme

OpenDQV is committed to making data quality tooling accessible in every language.
This directory contains community-contributed translations of the core documentation.

---

## Supported Languages

| Language | Status | Maintainer | Files |
|---|---|---|---|
| Arabic (العربية) | ✅ Active | Community | [ar/](ar/) |
| French (Français) | 🔄 In progress | Community | — |
| Spanish (Español) | 🔄 In progress | Community | — |
| Portuguese (Português) | 📋 Planned | Community | — |
| German (Deutsch) | 📋 Planned | Community | — |
| Japanese (日本語) | 📋 Planned | Community | — |
| Mandarin (中文) | 📋 Planned | Community | — |
| Hindi (हिन्दी) | 📋 Planned | Community | — |
| Swahili | 📋 Planned | Community | — |

---

## How to Contribute a Translation

1. **Fork** the OpenDQV repository
2. Create a directory `docs/i18n/<lang-code>/` (use ISO 639-1 codes)
3. Copy `docs/i18n/ar/` as a template — it shows the expected structure
4. Translate the files, keeping all YAML code blocks unchanged
5. Open a pull request with the title `[i18n] Add <language> translation`

### What to translate

Priority order (translate these first):
1. `quickstart.md` — getting started in 90 seconds
2. `contract_authoring.md` — writing your first contract
3. `api_reference.md` — validate endpoint reference

Do **not** translate:
- YAML contract examples (keep code blocks in English)
- API endpoint paths and HTTP methods
- Field names and rule type names (these are part of the API contract)

### Review process

1. Open a draft PR — community members can review
2. Two native-speaker approvals required before merge
3. Machine translation (GPT/DeepL) is acceptable as a starting point but must be reviewed by a native speaker
4. The core team reviews for technical accuracy

---

## Style Guide

- Use **formal register** (vouvoiement in French, usted in Spanish, etc.)
- Preserve all technical terms in their original English form on first use, with the translation in parentheses
  - Example (Arabic): المشغّل (operator) / السجل (record) / العقد (contract)
- Use gender-neutral language where possible
- Keep sentence structure close to the source to enable diff-based updates

---

## Translation Memory

Common technical terms with agreed translations:

| English | Arabic | French | Spanish |
|---|---|---|---|
| contract | عقد (aqd) | contrat | contrato |
| record | سجل (sijill) | enregistrement | registro |
| validation | تحقق (tahaqquq) | validation | validación |
| rule | قاعدة (qa'ida) | règle | regla |
| severity | خطورة (khutūra) | gravité | gravedad |
| error | خطأ (khaṭaʾ) | erreur | error |
| warning | تحذير (taḥdhīr) | avertissement | advertencia |
| owner | مالك (mālik) | propriétaire | propietario |
| context | سياق (siyāq) | contexte | contexto |
| pipeline | خط أنابيب (khaṭṭ anābīb) | pipeline | pipeline |

---

## Recognition

All translation contributors are listed in `CONTRIBUTORS.md` and receive:
- Recognition in the project README
- "Community Translator" badge in GitHub Discussions
- Priority access to beta features for feedback
