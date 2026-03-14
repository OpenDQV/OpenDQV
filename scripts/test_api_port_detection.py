"""
Container integration test: _start_uvicorn() PID-lock behaviour.

Scenario A — port 8000 free:
  3 wizard runs → only 1 uvicorn process total, all return _base_url=localhost:8000.

Scenario B — foreign process holds 8000:
  foreign listener on 8000 → 1st run spawns on 8001,
  runs 2+3 reuse 8001 via lock, no extra processes spawned.

Run inside a clean container:
    docker run --rm -v "$(pwd)":/app -w /app python:3.11-slim \
      sh -c "pip install -q -r requirements.txt && \
             python scripts/test_api_port_detection.py"
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── make project root importable ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

import core.onboarding as _mod
from core.onboarding import (
    OnboardingWizard,
    WizardResult,
    _API_LOCK,
    _read_api_lock,
    _write_api_lock,
)

LOCK_FILE = _API_LOCK
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_failures: list[str] = []


def check(label: str, condition: bool) -> None:
    status = PASS if condition else FAIL
    print(f"  [{status}] {label}")
    if not condition:
        _failures.append(label)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_wizard(tmp_path: Path) -> OnboardingWizard:
    config_file = tmp_path / "dq_contract.yaml"
    config_file.write_text("dataset: test\nrules: []\n")
    return OnboardingWizard(str(config_file))


def _cleanup(foreign_proc=None) -> None:
    """Kill any spawned uvicorn procs, remove lock file."""
    try:
        subprocess.run(["pkill", "-f", "uvicorn main:app"],
                       capture_output=True)
    except FileNotFoundError:
        pass
    if foreign_proc is not None:
        try:
            foreign_proc.terminate()
            foreign_proc.wait(timeout=2)
        except Exception:
            pass
    if LOCK_FILE.exists():
        LOCK_FILE.unlink()
    time.sleep(0.3)


def _spawn_foreign_listener(port: int) -> subprocess.Popen:
    """Bind a dummy TCP listener on *port* so it appears occupied."""
    code = (
        f"import socket, time\n"
        f"s = socket.socket()\n"
        f"s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('127.0.0.1', {port}))\n"
        f"s.listen(1)\n"
        f"time.sleep(60)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _port_occupied(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


# ── Scenario A: port 8000 free ────────────────────────────────────────────────

def scenario_a(tmp_path: Path) -> None:
    print("\nScenario A — port 8000 free, 3 runs should share 1 process")
    _cleanup()

    urls: list[str] = []
    mock_proc = MagicMock()
    mock_proc.pid = os.getpid()   # a definitely-alive PID

    for i in range(3):
        wiz = _make_wizard(tmp_path)
        with (
            patch.object(wiz, "_find_free_port", return_value=8000),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            # Run 1 spawns; runs 2+3 should read lock and reuse.
            # We patch Popen so no real uvicorn is launched, but
            # _write_api_lock persists a real (alive) PID.
            result = wiz._start_uvicorn()
            urls.append(wiz._base_url)
            spawned = mock_popen.called
            if i == 0:
                check(f"  run {i+1}: Popen called (initial spawn)", spawned)
            else:
                check(f"  run {i+1}: Popen NOT called (lock reused)", not spawned)

    check("All 3 runs set _base_url=localhost:8000",
          all(u == "http://localhost:8000" for u in urls))

    _cleanup()


# ── Scenario B: foreign process holds 8000 ────────────────────────────────────

def scenario_b(tmp_path: Path) -> None:
    print("\nScenario B — foreign process on 8000, runs should land on 8001")
    _cleanup()

    foreign = _spawn_foreign_listener(8000)
    time.sleep(0.4)   # give it time to bind
    check("Foreign listener bound 8000", _port_occupied(8000))

    mock_proc = MagicMock()
    mock_proc.pid = os.getpid()

    urls: list[str] = []

    for i in range(3):
        wiz = _make_wizard(tmp_path)
        # _find_free_port is called only on the first run (no lock yet);
        # subsequent runs skip straight to the lock check.
        with (
            patch.object(wiz, "_find_free_port", return_value=8001),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            result = wiz._start_uvicorn()
            urls.append(wiz._base_url)
            spawned = mock_popen.called
            if i == 0:
                check(f"  run {i+1}: Popen called (spawn on 8001)", spawned)
            else:
                check(f"  run {i+1}: Popen NOT called (lock reused)", not spawned)

    check("All 3 runs set _base_url=localhost:8001",
          all(u == "http://localhost:8001" for u in urls))

    _cleanup(foreign)


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        scenario_a(tmp)
        scenario_b(tmp)

    print()
    if _failures:
        print(f"\033[31mFAILED — {len(_failures)} check(s):\033[0m")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\033[32mAll checks PASSED\033[0m")
        sys.exit(0)
