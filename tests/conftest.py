import os
import sys
import tempfile

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Use a temporary DB for tests
os.environ["SECRET_KEY"] = "test-secret-key-for-testing"
os.environ["AUTH_MODE"] = "token"  # Tests run with auth enabled to verify auth logic
os.environ["OPENDQV_DB_PATH"] = os.path.join(tempfile.gettempdir(), "opendqv_test.db")
os.environ["OPENDQV_CONTRACTS_DIR"] = os.path.join(os.path.dirname(__file__), "..", "contracts")

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


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(test_token):
    return {"Authorization": f"Bearer {test_token}"}


@pytest.fixture
def approver_headers(approver_token):
    return {"Authorization": f"Bearer {approver_token}"}
