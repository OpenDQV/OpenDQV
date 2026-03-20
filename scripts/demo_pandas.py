"""
Pandas integration demo simulation — renders the validate-before-write pattern
using only stdlib + ANSI codes.

Designed to be recorded by VHS; no external dependencies required.
"""

import sys
import time

R     = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED   = "\033[31m"
WHITE = "\033[97m"


def p(*args, delay=0.0, **kw):
    print(*args, **kw)
    sys.stdout.flush()
    if delay:
        time.sleep(delay)


def rule():
    p(f"{DIM}{'─' * 66}{R}")


def section(label):
    p(f"\n  {CYAN}{BOLD}{label}{R}")


def ok(msg):
    p(f"  {GREEN}✓{R}  {msg}")


def fail(msg):
    p(f"  {RED}✗{R}  {msg}")


def info(msg):
    p(f"  {DIM}→{R}  {msg}")


def field_err(field, msg):
    p(f"        {RED}✗{R} {DIM}{field:<10}{R}  {msg}")


# ── Header ────────────────────────────────────────────────────────────────────
time.sleep(0.3)
p(f"""
{CYAN}{BOLD} ██████╗ ██████╗ ███████╗███╗   ██╗██████╗  ██████╗ ██╗   ██╗
██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔══██╗██╔═══██╗██║   ██║
██║   ██║██████╔╝█████╗  ██╔██╗ ██║██║  ██║██║   ██║██║   ██║
██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║██║  ██║██║▄▄ ██║╚██╗ ██╔╝
╚██████╔╝██║     ███████╗██║ ╚████║██████╔╝╚██████╔╝ ╚████╔╝
 ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚═════╝  ╚══▀▀═╝   ╚═══╝{R}""")
p(f"  {DIM}Open Data Quality Validation  ·  v1.0.0{R}")
p(f"  {CYAN}{BOLD}Pandas Integration — validate before writing{R}\n")
rule()

# ── Step 1: Load DataFrame ────────────────────────────────────────────────────
section("Step 1  Load DataFrame")
time.sleep(0.5)

info(f"Reading {CYAN}customers.csv{R}  →  4 rows")
time.sleep(0.6)

COL_W = [5, 16, 26, 5]
SEP = f"  {DIM}{'─' * COL_W[0]}─{'─' * COL_W[1]}─{'─' * COL_W[2]}─{'─' * COL_W[3]}{R}"

def row(id_, name, email, age, dim=False):
    prefix = DIM if dim else ""
    p(f"  {prefix}{str(id_):<{COL_W[0]}} {name:<{COL_W[1]}} {email:<{COL_W[2]}} {str(age):<{COL_W[3]}}{R}")

# Header row
p(f"\n  {BOLD}{'id':<{COL_W[0]}} {'name':<{COL_W[1]}} {'email':<{COL_W[2]}} {'age':<{COL_W[3]}}{R}")
p(SEP)

time.sleep(0.2)
row(1,  "Alice Hayward",   "alice@example.com",  34)
time.sleep(0.1)
row(2,  "Bob Ng",          "bob-not-an-email",   27)
time.sleep(0.1)
row(3,  "Carol Estrada",   "carol@example.com",  -5)
time.sleep(0.1)
row(4,  "Diana Okonkwo",   "diana@example.com",  41)
time.sleep(0.4)
p(SEP)
p(f"  {DIM}4 rows × 4 columns{R}\n")
time.sleep(0.5)

# ── Step 2: Validate batch ────────────────────────────────────────────────────
section("Step 2  Validate each row  ·  contract: customer")
time.sleep(0.4)

info(f"validator.validate_batch(records, contract={CYAN}\"customer\"{R})")
time.sleep(0.8)
p()

# Row 0 — PASS
p(f"  {DIM}Row 0{R}  Alice Hayward", delay=0.3)
ok(f"{GREEN}{BOLD}PASS{R}  —  0 errors")
time.sleep(0.3)

# Row 1 — FAIL (email)
p(f"\n  {DIM}Row 1{R}  Bob Ng", delay=0.3)
fail(f"{RED}{BOLD}FAIL{R}  —  1 error")
field_err("email", "Value 'bob-not-an-email' does not match email pattern")
time.sleep(0.4)

# Row 2 — FAIL (age)
p(f"\n  {DIM}Row 2{R}  Carol Estrada", delay=0.3)
fail(f"{RED}{BOLD}FAIL{R}  —  1 error")
field_err("age", "Value -5 is below minimum 0")
time.sleep(0.4)

# Row 3 — PASS
p(f"\n  {DIM}Row 3{R}  Diana Okonkwo", delay=0.3)
ok(f"{GREEN}{BOLD}PASS{R}  —  0 errors")
time.sleep(0.5)

# ── Step 3: Summary + split ───────────────────────────────────────────────────
p()
rule()
section("Step 3  Annotate  →  split  →  write")
time.sleep(0.4)

info(f"df[{CYAN}\"_opendqv_valid\"{R}] = df.index.map(validity)")
time.sleep(0.5)
info("clean_df    = df[df._opendqv_valid]")
time.sleep(0.3)
info("rejected_df = df[~df._opendqv_valid]")
time.sleep(0.7)

p()
ok(f"{BOLD}2 valid{R}    →  written to  {CYAN}clean_df{R}        (rows 0, 3)")
time.sleep(0.3)
ok(f"{BOLD}2 invalid{R}  →  routed to   {CYAN}quarantine_df{R}   (rows 1, 2)")
time.sleep(0.3)
ok(f"clean_df.to_csv({CYAN}\"customers_clean.csv\"{R})")
time.sleep(0.3)
ok(f"rejected_df.to_csv({CYAN}\"customers_quarantine.csv\"{R})")
time.sleep(0.6)

# ── Close ─────────────────────────────────────────────────────────────────────
rule()
p(f"\n  {GREEN}{BOLD}2/4 rows passed — 50% acceptance rate{R}")
p(f"  {DIM}Bad data quarantined before it reaches your destination.{R}\n")
info(f"Contract docs:   {CYAN}docs/pandas_integration.md{R}")
info(f"API docs:        {CYAN}http://localhost:8000/docs{R}")
p()
rule()
p()
