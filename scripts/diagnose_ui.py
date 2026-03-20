"""
UI container diagnostic script.
Run inside the UI container:
  docker compose exec ui python scripts/diagnose_ui.py
"""
import json
import sys
import urllib.request
import urllib.error


def check(label, url, timeout=5):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        body = r.read(2048).decode("utf-8", errors="ignore")
        print(f"  OK  [{r.status}] {label}")
        return body
    except urllib.error.HTTPError as e:
        body = e.read(512).decode("utf-8", errors="ignore")
        print(f"  ERR [{e.code}] {label}  →  {body[:120]}")
    except Exception as e:
        print(f"  ERR [---] {label}  →  {e}")
    return None


print()
print("OpenDQV UI Diagnostics")
print("=" * 50)

print()
print("1. Streamlit self-checks (inside container → localhost:8501)")
check("Streamlit health endpoint",  "http://localhost:8501/_stcore/health")
check("Streamlit root /",           "http://localhost:8501/")
check("Streamlit static resource",  "http://localhost:8501/static/js/main.chunk.js")

print()
print("2. API reachability (inside container → api:8000)")
body = check("API /health",         "http://api:8000/health")
if body:
    try:
        print("     ", json.loads(body))
    except Exception:
        pass
check("API /api/v1/contracts",      "http://api:8000/api/v1/contracts")

print()
print("3. Environment")
import os
api_url = os.environ.get("API_URL", "(not set)")
print(f"  API_URL env var: {api_url}")

import streamlit as st
print(f"  Streamlit version: {st.__version__}")
print(f"  Python version: {sys.version.split()[0]}")

print()
print("4. app.py import check")
try:
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("app", pathlib.Path("ui/app.py"))
    mod = importlib.util.module_from_spec(spec)
    # Only run the module-level code (imports + constants), not st.* calls
    # by catching the NoSessionState error Streamlit raises outside a session
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        err = str(e)
        if "ScriptRunner" in err or "NoSessionState" in err or "non-main thread" in err.lower():
            print("  OK  app.py imports successfully (Streamlit session guard expected outside browser)")
        else:
            print(f"  ERR app.py raised: {e}")
except Exception as e:
    print(f"  ERR could not load app.py: {e}")

print()
