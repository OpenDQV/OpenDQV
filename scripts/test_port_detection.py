#!/usr/bin/env python3
"""
Container smoke-test: proves _start_streamlit() correctly reuses an existing
workbench on 8501 instead of falling back to 8502.

Run via:
  docker run --rm -v $(pwd):/app -w /app python:3.11-slim python scripts/test_port_detection.py
"""
import socket
import sys
from unittest.mock import patch

sys.path.insert(0, ".")
from opendqv.core.onboarding import OnboardingWizard

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
errors = []


def _make_wizard():
    wiz = OnboardingWizard.__new__(OnboardingWizard)
    wiz.console = None
    wiz._info = lambda msg: print(f"  [info] {msg}")
    return wiz


def check(label, condition):
    if condition:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        errors.append(label)


# ── Test 1: port 8501 already bound → return 8501, no Popen ──────────────────
print("\nTest 1: port 8501 in use — should reuse, not spawn")
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupier:
    occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupier.bind(("127.0.0.1", 8501))
    occupier.listen(1)

    with patch("subprocess.Popen") as mock_popen:
        result = _make_wizard()._start_streamlit()

    check("returns 8501", result == 8501)
    check("Popen not called", mock_popen.call_count == 0)

# ── Test 2: port 8501 free → spawn on 8501 ───────────────────────────────────
print("\nTest 2: port 8501 free — should spawn Streamlit on 8501")
with patch("subprocess.Popen") as mock_popen:
    result = _make_wizard()._start_streamlit()

check("returns 8501", result == 8501)
check("Popen called once", mock_popen.call_count == 1)
if mock_popen.call_count == 1:
    args = mock_popen.call_args[0][0]
    check("streamlit in args", "streamlit" in args)
    check("ui/app.py in args", "ui/app.py" in args)
    check("port 8501 in args", "8501" in args)

# ── Test 3: second consecutive call with 8501 bound → no second Streamlit ────
print("\nTest 3: back-to-back calls with 8501 occupied — second call must not spawn")
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupier:
    occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    occupier.bind(("127.0.0.1", 8501))
    occupier.listen(1)

    wiz = _make_wizard()
    with patch("subprocess.Popen") as mock_popen:
        r1 = wiz._start_streamlit()
        r2 = wiz._start_streamlit()

    check("both calls return 8501", r1 == 8501 and r2 == 8501)
    check("Popen never called", mock_popen.call_count == 0)

# ── Summary ───────────────────────────────────────────────────────────────────
print()
if errors:
    print(f"{FAIL}  {len(errors)} check(s) failed: {errors}")
    sys.exit(1)
else:
    total = 9
    print(f"{PASS}  All {total} checks passed")
    sys.exit(0)
