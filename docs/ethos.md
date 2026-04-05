# OpenDQV Ethos

> **"Trust is easier to build than to repair."**

## Origin

This phrase emerged during the write-guardrail design session (March 2026) while the team was working through why preventing silent contract mutations mattered — not just technically, but fundamentally. The question was: *why do we care so much about this?* The answer that surfaced was the phrase above.

## What it means

The cost of maintaining user, contributor, and systemic trust continuously is always lower than the cost of reconstructing it after a single failure.

This is not a soft principle. It has a quantifiable cost structure:

- A tool that silently mutates a data contract loses a user permanently, not temporarily. The user does not file a bug and wait. They stop using it and they tell others to stop.
- A schema drift that goes undetected until a production dashboard breaks costs hours of forensic investigation, rollback, and stakeholder repair — all of which were avoidable with a contract that held.
- The 2017 Equifax breach cost over $1.4B to remediate. The certificate renewal that would have caught the lapse cost effectively nothing. Same asymmetry, larger scale.

## What it obligates the project to

Every design decision in OpenDQV Core is accountable to this phrase. Practically, that means:

- **Write guardrails are not optional.** ACTIVE contracts are immutable. Agents cannot mutate contracts silently. This is not a nice-to-have — it is the minimum bar for a tool that claims to protect data quality.
- **Error messages must be honest.** A raw Python traceback is not an error message. A "500 Internal Server Error" with no explanation is not an error message. Users deserve to understand what went wrong and how to fix it.
- **Breaking changes must be handled with care.** The ethos does not prohibit change — it requires that change be transparent: migration guides, deprecation warnings, version pinning. Breaking something without warning is a trust failure.
- **The wizard is the first handshake.** The experience a new user has in their first 90 seconds shapes their entire relationship with the tool. It must be excellent.
- **Documentation must reflect reality.** A doc that says "see later" or describes a feature that does not exist erodes trust silently. Keep docs honest.

## For contributors

When you are deciding how something should work — and you are uncertain — reach for this phrase. Ask: *does this decision build trust continuously, or does it create a repair bill later?*

If a shortcut is clearly necessary, document it and date it. Known debt is manageable. Hidden debt is expensive.

If a guardrail feels expensive to implement correctly, remember that the alternative is implementing it incorrectly and paying the cost later. The phrase is describing a cost comparison. The math always favours the investment.

## Where it appears in the product

- **Workbench sidebar** — beneath the logo header on every session
- **CLI** — in the `onboard` wizard welcome and `--version` output
- **README** — beneath the project title
- **This document** — the canonical explanation for contributors
