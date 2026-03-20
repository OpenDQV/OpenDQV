#!/usr/bin/env python3
"""
OpenDQV + DuckDB animated terminal demo.
Stdlib + ANSI only — no external dependencies.
"""

import time
import sys

# ── ANSI colour helpers ────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
RED     = "\033[31m"
CYAN    = "\033[36m"
YELLOW  = "\033[33m"
MAGENTA = "\033[35m"
WHITE   = "\033[97m"
BG_DARK = "\033[48;5;235m"

def cyan(s):    return f"{CYAN}{BOLD}{s}{RESET}"
def green(s):   return f"{GREEN}{s}{RESET}"
def red(s):     return f"{RED}{s}{RESET}"
def dim(s):     return f"{DIM}{s}{RESET}"
def bold(s):    return f"{BOLD}{s}{RESET}"
def yellow(s):  return f"{YELLOW}{s}{RESET}"
def magenta(s): return f"{MAGENTA}{s}{RESET}"

def hr(char="─", width=62, colour=CYAN):
    return f"{colour}{char * width}{RESET}"

def print_flush(*args, **kwargs):
    print(*args, **kwargs)
    sys.stdout.flush()

def typewriter(text, delay=0.025):
    for ch in text:
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(delay)
    print()

# ── STEP 0: clear + header ─────────────────────────────────────────────────────
print()
print_flush(hr("═"))
print_flush(f"  {cyan('OpenDQV')} {bold('+')} {cyan('DuckDB')}  {DIM}—{RESET}  batch validation at scale")
print_flush(hr("═"))
print()
time.sleep(0.5)

# ── STEP 1: load data ─────────────────────────────────────────────────────────
print_flush(cyan("  STEP 1 — Load orders table via DuckDB"))
print_flush(hr())
time.sleep(0.3)

sql_lines = [
    "  conn = duckdb.connect(':memory:')",
    "  conn.execute(\"\"\"",
    "      CREATE TABLE orders AS",
    "      SELECT * FROM read_parquet('orders.parquet')",
    "  \"\"\")",
    "  df = conn.execute('SELECT * FROM orders').fetchdf()",
]
for line in sql_lines:
    print_flush(dim(line))
    time.sleep(0.12)

print()
time.sleep(0.4)

# Simulate column listing
columns = ["order_id", "customer_id", "amount", "currency", "email", "status", "created_at"]
print_flush(f"  {bold('Columns:')}  " + "  ".join(f"{CYAN}{c}{RESET}" for c in columns))
time.sleep(0.25)
print_flush(f"  {bold('Loaded')}   {GREEN}{BOLD}10,000{RESET} orders from {YELLOW}orders.parquet{RESET}")
print()
time.sleep(0.6)

# ── STEP 2: run batch validation ──────────────────────────────────────────────
print_flush(cyan("  STEP 2 — Run OpenDQV batch validation"))
print_flush(hr())
time.sleep(0.3)

sdk_lines = [
    "  from sdk.local import LocalValidator",
    "  validator = LocalValidator()",
    "  records  = df.to_dict('records')",
    "  result   = validator.validate_batch(records, contract='orders')",
]
for line in sdk_lines:
    print_flush(dim(line))
    time.sleep(0.14)

print()
time.sleep(0.3)

# Progress bar
BAR_WIDTH = 40
total     = 10_000

print_flush(f"  Validating {bold('10,000')} records …")
print()

milestones = [500, 1000, 2000, 3500, 5000, 6500, 8000, 9200, 10000]
prev = 0
for milestone in milestones:
    pct   = milestone / total
    filled = int(BAR_WIDTH * pct)
    bar   = f"{GREEN}{'█' * filled}{DIM}{'░' * (BAR_WIDTH - filled)}{RESET}"
    label = f"  [{bar}{RESET}] {CYAN}{milestone:>6,}{RESET}/{total:,}"
    # Overwrite same line
    sys.stdout.write(f"\r{label}")
    sys.stdout.flush()
    time.sleep(0.18 + (milestone - prev) / 80_000)
    prev = milestone

print()  # newline after bar
print()
time.sleep(0.5)

# ── STEP 3: results summary ───────────────────────────────────────────────────
print_flush(cyan("  STEP 3 — Validation results"))
print_flush(hr())
time.sleep(0.3)

passed  = 9_953
failed  = 47
pct_ok  = passed / total * 100

print_flush(f"  {bold('Total records  ')}  {WHITE}{total:,}{RESET}")
time.sleep(0.15)
print_flush(f"  {bold('Passed         ')}  {green(f'{passed:,}')}  {DIM}({pct_ok:.1f}%){RESET}")
time.sleep(0.15)
print_flush(f"  {bold('Failed         ')}  {red(f'{failed:,}')}  {DIM}({100 - pct_ok:.1f}%){RESET}")
time.sleep(0.3)
print()

# Top failing rules
print_flush(f"  {bold('Top failing rules:')}")
time.sleep(0.2)
rules = [
    ("email_format",   21, "email does not match RFC 5321 pattern"),
    ("amount_range",   14, "amount outside [0.01, 1,000,000]"),
    ("currency_enum",   9, "currency not in allowed set"),
    ("status_not_null", 3, "status field is null or empty"),
]
for rank, (rule, count, desc) in enumerate(rules, 1):
    bar = RED + "▪" * min(count, 25) + RESET
    print_flush(f"    {DIM}{rank}.{RESET}  {CYAN}{rule:<20}{RESET}  {red(str(count)):>3} rows  {dim(desc)}")
    time.sleep(0.2)

print()
time.sleep(0.5)

# ── STEP 4: quarantine table ──────────────────────────────────────────────────
print_flush(cyan("  STEP 4 — Write quarantine table"))
print_flush(hr())
time.sleep(0.3)

quarantine_sql = [
    "  conn.execute(\"\"\"",
    "      CREATE TABLE orders_quarantine AS",
    "      SELECT o.*, v.rule, v.message",
    "      FROM orders o",
    "      JOIN validation_errors v ON o.order_id = v.record_id",
    "  \"\"\")",
]
for line in quarantine_sql:
    print_flush(dim(line))
    time.sleep(0.13)

print()
time.sleep(0.4)
typewriter(f"  {GREEN}✓{RESET}  INSERT INTO orders_quarantine … {CYAN}47 rows{RESET}", delay=0.022)
time.sleep(0.25)
typewriter(f"  {GREEN}✓{RESET}  INSERT INTO orders_clean      … {CYAN}9,953 rows{RESET}", delay=0.022)
print()
time.sleep(0.5)

# ── STEP 5: closing ───────────────────────────────────────────────────────────
print_flush(hr("═"))
print_flush(f"  {GREEN}{BOLD}✓{RESET}  Validation complete  "
            f"{DIM}|{RESET}  {CYAN}99.5%{RESET} clean  "
            f"{DIM}|{RESET}  {YELLOW}47{RESET} rows quarantined")
print_flush(hr("═"))
print()
print_flush(f"  {DIM}OpenDQV v1.0.0  ·  github.com/OpenDQV/OpenDQV{RESET}")
print()
time.sleep(1.0)
