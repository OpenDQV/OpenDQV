"""
Wizard demo simulation — renders the onboarding wizard flow for the
Social Media / Age Compliance template using only stdlib + ANSI codes.

Designed to be recorded by VHS; no external dependencies required.
"""

import sys
import time

R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
WHITE = "\033[97m"
BG_CYAN = "\033[46m"


def p(*args, delay=0.0, **kw):
    print(*args, **kw)
    sys.stdout.flush()
    if delay:
        time.sleep(delay)


def rule():
    p(f"{DIM}{'─' * 62}{R}")


def step(n, total, label):
    p(f"\n  {CYAN}{BOLD}[{n}/{total}]{R}  {BOLD}{label}{R}")


def ok(msg):
    p(f"  {GREEN}✓{R}  {msg}")


def info(msg):
    p(f"  {DIM}→{R}  {msg}")


def warn(msg):
    p(f"  {YELLOW}⚠{R}  {msg}")


# ── Logo ──────────────────────────────────────────────────────────────────────
time.sleep(0.3)
p(f"""
{CYAN}{BOLD} ██████╗ ██████╗ ███████╗███╗   ██╗██████╗  ██████╗ ██╗   ██╗
██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔══██╗██╔═══██╗██║   ██║
██║   ██║██████╔╝█████╗  ██╔██╗ ██║██║  ██║██║   ██║██║   ██║
██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║██║  ██║██║▄▄ ██║╚██╗ ██╔╝
╚██████╔╝██║     ███████╗██║ ╚████║██████╔╝╚██████╔╝ ╚████╔╝
 ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚═════╝  ╚══▀▀═╝   ╚═══╝{R}""")
p(f"  {DIM}Open Data Quality Validation  ·  v1.0.0{R}\n")
rule()

# ── Step 1: Environment ───────────────────────────────────────────────────────
step(1, 4, "Check your environment")
time.sleep(0.6)
ok("Python 3.11.9")
time.sleep(0.3)
ok("Docker 27.3.1 detected")
time.sleep(0.3)
ok(".env found")

# ── Step 2: Template picker ───────────────────────────────────────────────────
step(2, 4, "What are you validating?")
time.sleep(0.5)

templates = [
    ("Agriculture",          "— harvests, batches, soil data",         9),
    ("Automotive",           "— vehicle records, VIN, service",         9),
    ("Banking",              "— transactions, accounts",                8),
    ("Customer",             "— generic customer record",               12),
    ("Education",            "— student records, GPA, enrolment",       8),
    ("Healthcare",           "— patient records, clinical data",        10),
    ("HR & Workforce",       "— employee records, payroll",             9),
    ("Logistics",            "— shipments, supply chain",               9),
    ("Social Media",         "— age compliance, DOB verification",      14),
]

for label, desc, rules in templates:
    p(f"  {DIM}  {label:<22} {desc}{R}", delay=0.05)

p(f"  {DIM}  Build my own...{R}")
time.sleep(0.8)

# Simulate user filtering to "Social Media"
p(f"\n  {DIM}Filter:{R} {CYAN}Social{R}", delay=0.0)
time.sleep(0.5)
p(f"\n  {BG_CYAN}{WHITE} ❯ Social Media — age compliance, DOB verification  (14 rules) {R}")
time.sleep(0.7)
ok(f"Template: {BOLD}Social Media — age compliance, DOB verification{R}")

# ── Step 3: Contract ready ────────────────────────────────────────────────────
step(3, 4, "Contract ready")
time.sleep(0.4)
ok("14 rules loaded from template")
info(f"Edit {CYAN}contracts/social_media_age_compliance.yaml{R} to customise")

# ── Step 4: Start + validate ──────────────────────────────────────────────────
step(4, 4, "Starting OpenDQV...")
time.sleep(0.5)
p(f"  {DIM}  Starting OpenDQV...{R}", end="", flush=True)
for _ in range(5):
    time.sleep(0.3)
    p(f"{DIM}.{R}", end="", flush=True)
p()
ok(f"OpenDQV is running on {CYAN}http://localhost:8000{R}")
time.sleep(0.3)
p(f"  {DIM}  Reloading contracts...{R}", end="", flush=True)
time.sleep(0.6)
p()
ok("Contracts reloaded")

time.sleep(0.4)
p(f"\n  {DIM}Testing your contract with sample data...{R}\n")
time.sleep(0.5)

# Invalid sample
p(f"  {BOLD}Sample record (should fail):{R}")
p(f"  {DIM}  user_id: USR-DEMO-01  age: 11  dob: 2014-08-20  verified_identity: FALSE{R}")
time.sleep(0.6)
p(f"  {RED}✗{R}  {BOLD}invalid{R}  —  2 errors")
p(f"      {RED}✗{R} age         Declared age must be 13 or above for platform access")
p(f"      {RED}✗{R} dob         Date of birth indicates user is under 13")

time.sleep(0.6)

# Valid sample
p(f"\n  {BOLD}Sample record (should pass):{R}")
p(f"  {DIM}  user_id: USR-DEMO-02  age: 25  dob: 2000-06-15  verified_identity: TRUE{R}")
p(f"  {DIM}  verification_method: GOVERNMENT_ID  verification_timestamp: 2026-03-14T09:30:00Z{R}")
time.sleep(0.6)
p(f"  {GREEN}✓{R}  {BOLD}valid{R}  —  0 errors")

# ── Next steps ────────────────────────────────────────────────────────────────
time.sleep(0.5)
rule()
p(f"\n  {GREEN}{BOLD}OpenDQV is running. Your contract is live.{R}\n")
info(f"Edit your contract:   {CYAN}contracts/social_media_age_compliance.yaml{R}")
info(f"Visual workbench:     {CYAN}http://localhost:8501{R}")
info(f"API docs:             {CYAN}http://localhost:8000/docs{R}")
info(f"All rule types:       {CYAN}docs/rules/README.md{R}")
p()
p(f"  {DIM}Time from start to first validation: 18s{R}")
rule()
p()
