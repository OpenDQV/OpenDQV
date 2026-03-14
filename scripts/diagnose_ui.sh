#!/usr/bin/env bash
# Run from repo root on your Mac:
#   bash scripts/diagnose_ui.sh
set -euo pipefail

echo ""
echo "OpenDQV UI Diagnostics"
echo "=================================="

echo ""
echo "1. Container status"
docker compose ps

echo ""
echo "2. Streamlit self-check (inside container)"
docker compose exec -T ui python - <<'PYEOF'
import urllib.request, urllib.error, json, os, sys

def check(label, url):
    try:
        r = urllib.request.urlopen(url, timeout=5)
        body = r.read(512).decode("utf-8", errors="ignore")
        print(f"  OK  [{r.status}] {label}")
        return body
    except urllib.error.HTTPError as e:
        body = e.read(256).decode("utf-8", errors="ignore")
        print(f"  ERR [{e.code}] {label} -> {body[:100]}")
    except Exception as e:
        print(f"  FAIL       {label} -> {e}")
    return None

check("Streamlit root /",       "http://localhost:8501/")
check("Streamlit /_stcore/health", "http://localhost:8501/_stcore/health")

print()
b = check("API /health",        "http://api:8000/health")
if b:
    try:
        print("    ", json.loads(b))
    except Exception:
        pass
check("API /api/v1/contracts",  "http://api:8000/api/v1/contracts")

print()
print("  API_URL env:", os.environ.get("API_URL", "(not set)"))
import streamlit as st
print("  Streamlit:  ", st.__version__)
print("  Python:     ", sys.version.split()[0])
PYEOF

echo ""
echo "3. UI container logs (last 30 lines)"
docker compose logs ui --tail=30
