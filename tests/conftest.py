import atexit
import asyncio
import os
import shutil
import sys
import tempfile

import pytest

# Windows: ProactorEventLoop (default on 3.8+) has subprocess-cleanup behaviour
# that triggers a spurious KeyboardInterrupt through _pytest/subtests.py at
# session teardown. SelectorEventLoop is safe here — all tests use sync TestClient.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use a fresh isolated DB per test session — prevents stale history snapshots from
# prior runs causing false positives in versioning tests.
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-opendqv"
os.environ["AUTH_MODE"] = "token"  # Tests run with auth enabled to verify auth logic
_tmp_db_dir = tempfile.mkdtemp(prefix="opendqv_test_db_")
_db_path = os.path.join(_tmp_db_dir, "opendqv.db")
os.environ["OPENDQV_DB_PATH"] = _db_path
atexit.register(shutil.rmtree, _tmp_db_dir, ignore_errors=True)
print(f"\n[conftest] Test DB: {_db_path}", flush=True)

# Copy contracts to a temp directory so tests that mutate contracts (e.g.
# TestRuleMutationOnDraft bumping the draft version counter) never write
# back to the live contracts/ directory on the host filesystem.
_contracts_src = os.path.join(os.path.dirname(__file__), "..", "contracts")
_tmp_contracts_root = tempfile.mkdtemp(prefix="opendqv_test_contracts_")
shutil.copytree(_contracts_src, os.path.join(_tmp_contracts_root, "contracts"))
os.environ["OPENDQV_CONTRACTS_DIR"] = os.path.join(_tmp_contracts_root, "contracts")
atexit.register(shutil.rmtree, _tmp_contracts_root, ignore_errors=True)

from fastapi.testclient import TestClient
from main import app
from security.auth import create_pat


@pytest.fixture(scope="session")
def test_token():
    result = create_pat("testuser")
    return result["token"]


@pytest.fixture(scope="session")
def approver_token():
    result = create_pat("approver-testuser", role="approver")
    return result["token"]


@pytest.fixture(scope="session")
def editor_token():
    result = create_pat("editor-testuser", role="editor")
    return result["token"]


@pytest.fixture(scope="session")
def reader_token():
    result = create_pat("reader-testuser", role="reader")
    return result["token"]


@pytest.fixture(scope="session")
def auditor_token():
    result = create_pat("auditor-testuser", role="auditor")
    return result["token"]


@pytest.fixture(scope="session")
def admin_token():
    result = create_pat("admin-testuser", role="admin")
    return result["token"]


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(test_token):
    return {"Authorization": f"Bearer {test_token}"}


@pytest.fixture
def approver_headers(approver_token):
    return {"Authorization": f"Bearer {approver_token}"}


@pytest.fixture
def editor_headers(editor_token):
    return {"Authorization": f"Bearer {editor_token}"}


@pytest.fixture
def reader_headers(reader_token):
    return {"Authorization": f"Bearer {reader_token}"}


@pytest.fixture
def auditor_headers(auditor_token):
    return {"Authorization": f"Bearer {auditor_token}"}


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}
