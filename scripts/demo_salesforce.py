"""
Salesforce Phase 2 demo — live callout integration.

OpenDQV validates every Contact in a Before Insert trigger via HTTP callout
to the local API exposed over ngrok.  Shows deploy, two blocked writes, and
one successful write.  Stdlib + ANSI codes only — designed for VHS recording.
"""

import sys
import time

R     = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
RED   = "\033[31m"


def p(*args, delay=0.0, **kw):
    print(*args, **kw)
    sys.stdout.flush()
    if delay:
        time.sleep(delay)


def hr():
    p(f"{DIM}{'─' * 74}{R}")


# ── Header ─────────────────────────────────────────────────────────────────────
time.sleep(0.3)
p(f"\n{BOLD}{CYAN}Salesforce Phase 2 — Live callout via ngrok → OpenDQV{R}")
p(f"{DIM}  Before Insert trigger blocks invalid Contacts at write-time{R}\n")
time.sleep(0.6)

# ── Deploy ─────────────────────────────────────────────────────────────────────
p(f"{DIM}${R} sf project deploy start --source-dir force-app --target-org mydevorg")
time.sleep(1.4)
p(f"Deploy ID:    {BOLD}0AfdL00000Xc2LySAJ{R}")
p("Target Org:   sunny.sharma.a10733ac6ba1@agentforce.com")
p("Elapsed Time: 3.39s\n")
p(f"{BOLD}Deployed Source{R}\n")
p(f"  {CYAN}State    Name                            Type          Path{R}")
p(f"  {DIM}{'─' * 70}{R}")
rows = [
    ("OpenDQVCallout",               "ApexClass",   "classes/OpenDQVCallout.cls"),
    ("OpenDQVCallout",               "ApexClass",   "classes/OpenDQVCallout.cls-meta.xml"),
    ("ContactOpenDQVCalloutTrigger", "ApexTrigger", "triggers/ContactOpenDQVCalloutTrigger.trigger"),
    ("ContactOpenDQVCalloutTrigger", "ApexTrigger", "triggers/ContactOpenDQVCalloutTrigger.trigger-meta.xml"),
]
base = "force-app/main/default/"
for name, kind, path in rows:
    p(f"  {GREEN}Created{R}  {name:<31} {kind:<12} {base}{path}", delay=0.12)

time.sleep(0.9)
hr()

# ── Test 1: invalid email (Salesforce native validation) ───────────────────────
time.sleep(0.5)
p(f"\n{DIM}${R} sf data create record \\")
p("      --sobject Contact \\")
p("      --values \"FirstName='Test' LastName='Phase2Fail' Email='not-an-email'\" \\")
p("      --target-org mydevorg")
time.sleep(1.6)
p(f"Creating record for Contact... {RED}Error{R}")
p(f"{RED}Error (1): Email: invalid email address: not-an-email{R}")

time.sleep(1.3)
hr()

# ── Test 2: missing required field (OpenDQV trigger blocks) ───────────────────
time.sleep(0.5)
p(f"\n{DIM}${R} sf data create record \\")
p("      --sobject Contact \\")
p("      --values \"LastName='Phase2Fail' Email='test@example.com'\" \\")
p("      --target-org mydevorg")
time.sleep(1.6)
p(f"Creating record for Contact... {RED}Error{R}")
p(f"{RED}Error (1): OpenDQV validation failed: (FirstName is required.){R}")

time.sleep(1.3)
hr()

# ── Test 3: valid contact (PASS) ───────────────────────────────────────────────
time.sleep(0.5)
p(f"\n{DIM}${R} sf data create record \\")
p("      --sobject Contact \\")
p("      --values \"FirstName='Test' LastName='Phase2Pass' Email='test.phase2@example.com'\" \\")
p("      --target-org mydevorg")
time.sleep(1.8)
p(f"Creating record for Contact... {GREEN}done{R}")
p(f"{GREEN}Successfully created record: 003dL00001TbdobQAA.{R}")

time.sleep(1.0)
hr()
p(f"\n  {DIM}2 invalid records blocked before they reached the database.{R}")
p(f"  {GREEN}✓{R}  {BOLD}Write-time validation active across Salesforce and OpenDQV.{R}\n")
time.sleep(0.8)
