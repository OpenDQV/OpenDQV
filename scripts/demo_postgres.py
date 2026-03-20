"""
Postgres integration demo — validate at the application layer before INSERT.
Clean records land in the target table; rejects go to a quarantine table.
Stdlib + ANSI codes only — designed for VHS recording.
"""

import sys
import time

R      = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"


def p(*args, delay=0.0, **kw):
    print(*args, **kw)
    sys.stdout.flush()
    if delay:
        time.sleep(delay)


def hr():
    p(f"{DIM}{'─' * 68}{R}")


# ── Header ─────────────────────────────────────────────────────────────────────
time.sleep(0.3)
p(f"\n{BOLD}{CYAN}OpenDQV + Postgres — validate before INSERT{R}")
p(f"{DIM}  Clean records → customers  |  Rejects → customers_quarantine{R}\n")
time.sleep(0.5)

# ── Incoming batch ─────────────────────────────────────────────────────────────
p(f"{DIM}${R} python3 load_customers.py --contract customer --file customers.csv")
time.sleep(0.9)
p(f"{DIM}Loaded 5 records from customers.csv{R}")
p(f"{DIM}Validating against contract: customer v1.0{R}\n")
time.sleep(0.6)

hr()

records = [
    ("cust-001", "Alice Smith",  "alice@example.com",  29, True,  []),
    ("cust-002", "Bob",          "not-an-email",       35, False, ["name: too short (min 4 chars)", "email: invalid format"]),
    ("cust-003", "Carol Jones",  "carol@example.com",  17, False, ["age: must be >= 18"]),
    ("cust-004", "David Brown",  "david@example.com",  42, True,  []),
    ("cust-005", "Eve Wilson",   "eve@example.com",    31, True,  []),
]

for rid, name, email, age, valid, errors in records:
    time.sleep(0.45)
    p(f"\n  {DIM}{rid}  {name:<14} {email:<26} age={age}{R}")
    time.sleep(0.55)
    if valid:
        p(f"  {GREEN}✓  valid{R}   → INSERT INTO customers")
    else:
        p(f"  {RED}✗  invalid{R} → INSERT INTO customers_quarantine")
        for e in errors:
            p(f"       {RED}✗{R} {e}")

time.sleep(0.8)
hr()

# ── DB summary ────────────────────────────────────────────────────────────────
p(f"\n{BOLD}Postgres result{R}")
time.sleep(0.4)
p(f"  {DIM}SELECT count(*) FROM customers;{R}")
time.sleep(0.5)
p(f"  {GREEN}3 rows{R}  — Alice, David, Eve")
time.sleep(0.3)
p(f"\n  {DIM}SELECT count(*) FROM customers_quarantine;{R}")
time.sleep(0.5)
p(f"  {YELLOW}2 rows{R}  — Bob (bad email + short name), Carol (underage)")
time.sleep(0.6)
hr()
p(f"\n  {DIM}Bad data never reached the database.{R}")
p(f"  {GREEN}✓{R}  {BOLD}Contract-enforced data quality at the point of write.{R}\n")
time.sleep(0.7)
