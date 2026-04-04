#!/usr/bin/env bash
# record_demo_readme.sh
#
# Script showing the exact sequence to record for the new README demo GIF.
# Story: write YAML contract → reload → bad record → 422 → fix it → 200
#
# To record: use asciinema + svg-term, or screen capture with any GIF recorder.
# Output file: docs/demo.gif
#
# Usage (asciinema):
#   asciinema rec /tmp/opendqv-demo.cast
#   # ... paste the commands below ...
#   # exit
#   svg-term --in /tmp/opendqv-demo.cast --out docs/demo.svg --width 100 --height 30
#   # convert SVG to GIF with your preferred tool, rename to docs/demo.gif
#
# --- SEQUENCE TO RECORD ---

set -e

BASE="http://localhost:8000"

echo "==> Step 1: write the order contract"
cat > /tmp/order.yaml << 'EOF'
contract:
  name: order
  version: "1.0"
  owner: "Data Governance"
  status: active
  rules:
    - name: valid_email
      type: regex
      field: email
      pattern: "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"
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
EOF
cp /tmp/order.yaml contracts/order.yaml
echo "   -> contracts/order.yaml written"
sleep 1

echo ""
echo "==> Step 2: reload contracts"
curl -s -X POST "${BASE}/api/v1/contracts/reload" | python3 -m json.tool
sleep 1

echo ""
echo "==> Step 3: bad record — OpenDQV rejects it"
curl -s -X POST "${BASE}/api/v1/validate" \
  -H "Content-Type: application/json" \
  -d '{
    "contract": "order",
    "record": {
      "email": "not-an-email",
      "amount": -5,
      "status": "unknown"
    }
  }' | python3 -m json.tool
sleep 2

echo ""
echo "==> Step 4: fix the record — it passes"
curl -s -X POST "${BASE}/api/v1/validate" \
  -H "Content-Type: application/json" \
  -d '{
    "contract": "order",
    "record": {
      "email": "alice@example.com",
      "amount": 49.99,
      "status": "pending"
    }
  }' | python3 -m json.tool

echo ""
echo "==> Done. Bad record blocked, fixed record accepted."
