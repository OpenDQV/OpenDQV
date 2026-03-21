import atexit
import os
import shutil
import sys
import tempfile

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use a temporary DB for tests
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-opendqv"
os.environ["AUTH_MODE"] = "token"  # Tests run with auth enabled to verify auth logic
os.environ["OPENDQV_DB_PATH"] = os.path.join(tempfile.gettempdir(), "opendqv_test.db")

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
