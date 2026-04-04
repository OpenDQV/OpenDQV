#!/usr/bin/env python3
"""
Demo script for the README GIF.
Story: write YAML contract → reload → bad record → 422 → fix record → 200

Sleeps are kept short (~0.3s per section) so the total runtime is ~4s.
VHS PlaybackSpeed 0.6 stretches that to ~7s visible, easy to read.
"""

import subprocess
import json
import pathlib
import time

BASE = "http://localhost:8000"

RESET  = "\x1b[0m"
BOLD   = "\x1b[1m"
DIM    = "\x1b[2m"
GREEN  = "\x1b[32m"
BGREEN = "\x1b[1;32m"
CYAN   = "\x1b[36m"
BCYAN  = "\x1b[1;36m"
YELLOW = "\x1b[33m"
RED    = "\x1b[31m"
BRED   = "\x1b[1;31m"
GREY   = "\x1b[90m"

YAML = """\
contract:
  name: order
  version: "1.0"
  owner: "Data Governance"
  status: active
  rules:
    - name: valid_email
      type: regex
      field: email
      pattern: "^[^@\\\\s]+@[^@\\\\s]+\\\\.[^@\\\\s]+$"
      severity: error
      error_message: "Invalid email format"
    - name: amount_positive
      type: min
      field: amount
      min: 0.01
      severity: error
      error_message: "Order amount must be positive"
    - name: status_valid
      type: allowed_values
      field: status
      allowed_values: [pending, confirmed, shipped, cancelled]
      severity: error
      error_message: "Invalid order status"
"""

def hr(char="─", width=56):
    return f"{DIM}{char * width}{RESET}"

def call(args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r.stdout.strip()

def print_json(raw):
    try:
        data = json.loads(raw)
        for line in json.dumps(data, indent=2).splitlines():
            if '"valid": false' in line:
                print(f"{BRED}{line}{RESET}")
            elif '"valid": true' in line:
                print(f"{BGREEN}{line}{RESET}")
            elif '"message":' in line:
                print(f"{RED}{line}{RESET}")
            elif '"field":' in line or '"rule":' in line:
                print(f"{YELLOW}{line}{RESET}")
            elif '"errors": []' in line or '"warnings": []' in line:
                print(f"{GREEN}{line}{RESET}")
            elif '"status":' in line or '"contracts":' in line or '"reloaded"' in line:
                k, _, v = line.partition(":")
                print(f"{GREY}{k}:{RESET}{CYAN}{v}{RESET}")
            else:
                print(f"{DIM}{line}{RESET}")
    except Exception:
        print(raw)

print()
print(f"{BCYAN}  OpenDQV  —  write-time data validation{RESET}")
print(f"{DIM}  bad data rejected at the door. every time.{RESET}")
print(hr("═", 56))

# ── Step 1: write the contract ─────────────────────────────────────────────
time.sleep(0.25)
print()
print(f"{BCYAN}▶ Step 1 — define a data contract{RESET}")
print(hr())
print(f"{GREY}$ {RESET}cat > contracts/order.yaml")
for line in YAML.strip().splitlines():
    print(f"  {DIM}{line}{RESET}")
pathlib.Path("contracts/order.yaml").write_text(YAML, encoding="utf-8")
print(f"{BGREEN}  ✓  contracts/order.yaml written{RESET}")

# ── Step 2: reload contracts ───────────────────────────────────────────────
time.sleep(0.25)
print()
print(f"{BCYAN}▶ Step 2 — reload contracts{RESET}")
print(hr())
print(f"{GREY}$ {RESET}{CYAN}curl -X POST http://localhost:8000/api/v1/contracts/reload{RESET}")
out = call(["curl", "-s", "-X", "POST", f"{BASE}/api/v1/contracts/reload"])
print_json(out)

# ── Step 3: bad record → 422 ───────────────────────────────────────────────
time.sleep(0.25)
print()
print(f"{BCYAN}▶ Step 3 — bad record is rejected (422){RESET}")
print(hr())
print(f"{GREY}$ {RESET}{CYAN}curl -X POST .../validate  # email=not-an-email, amount=-5{RESET}")
bad = {"contract": "order", "record": {"email": "not-an-email", "amount": -5, "status": "unknown"}}
out = call(["curl", "-s", "-X", "POST", f"{BASE}/api/v1/validate",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(bad)])
print_json(out)

# ── Step 4: good record → 200 ──────────────────────────────────────────────
time.sleep(0.25)
print()
print(f"{BCYAN}▶ Step 4 — fix the record — it passes{RESET}")
print(hr())
print(f"{GREY}$ {RESET}{CYAN}curl -X POST .../validate  # alice@example.com, 49.99, pending{RESET}")
good = {"contract": "order", "record": {"email": "alice@example.com", "amount": 49.99, "status": "pending"}}
out = call(["curl", "-s", "-X", "POST", f"{BASE}/api/v1/validate",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(good)])
print_json(out)

time.sleep(0.25)
print()
print(hr("═", 56))
print(f"{BGREEN}  ✓  bad record blocked  ·  good record accepted{RESET}")
print()
