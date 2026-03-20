"""
Kafka integration demo — validate before committing the offset.
Bad records route to dead-letter topic; good records advance the consumer.
Stdlib + ANSI codes only — designed for VHS recording.
"""

import sys
import time

R     = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
RED   = "\033[31m"
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
p(f"\n{BOLD}{CYAN}OpenDQV + Kafka — validate before committing the offset{R}")
p(f"{DIM}  Bad records → dead-letter topic  |  Good records → validated topic{R}\n")
time.sleep(0.5)

# ── Consumer starting ──────────────────────────────────────────────────────────
p(f"{DIM}${R} python3 kafka_validator.py --contract orders --topic raw.orders")
time.sleep(1.0)
p(f"{DIM}Connecting to Kafka broker localhost:9092...{R}", delay=0.4)
p(f"{DIM}Subscribing to topic: raw.orders{R}", delay=0.3)
p(f"{GREEN}✓{R}  Consumer ready — waiting for records\n")
time.sleep(0.6)

hr()

# ── Records arriving ──────────────────────────────────────────────────────────
records = [
    ("orders", "offset=0", "order_id=ORD-001  status=confirmed  amount=149.99",  True,  []),
    ("orders", "offset=1", "order_id=ORD-002  status=UNKNOWN     amount=149.99",  False, ["status: value 'UNKNOWN' not in allowed list"]),
    ("orders", "offset=2", "order_id=ORD-003  status=shipped     amount=-50.00",  False, ["amount: must be >= 0.01"]),
    ("orders", "offset=3", "order_id=ORD-004  status=confirmed   amount=89.00",   True,  []),
    ("orders", "offset=4", "order_id=ORD-005  status=pending     amount=220.50",  True,  []),
]

for topic, offset, record, valid, errors in records:
    time.sleep(0.5)
    p(f"\n{DIM}↓  [{topic}] {offset}{R}")
    p(f"   {DIM}{record}{R}")
    time.sleep(0.7)
    if valid:
        p(f"   {GREEN}✓  valid{R}   → offset committed  → validated.orders")
    else:
        p(f"   {RED}✗  invalid{R} → offset NOT committed → dead-letter.orders")
        for e in errors:
            p(f"        {RED}✗{R} {e}")

time.sleep(0.8)
hr()

# ── Summary ───────────────────────────────────────────────────────────────────
p(f"\n{BOLD}Session summary{R}")
time.sleep(0.3)
p(f"  {GREEN}✓{R}  3 records validated  → committed to {CYAN}validated.orders{R}")
p(f"  {RED}✗{R}  2 records rejected   → routed to {YELLOW}dead-letter.orders{R}")
p(f"  {DIM}  Consumer lag: 0  |  Last committed offset: 4{R}")
time.sleep(0.6)
hr()
p(f"\n  {DIM}Downstream consumers receive only clean, contract-validated records.{R}\n")
time.sleep(0.6)
