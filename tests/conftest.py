"""
conftest.py — shared pytest fixtures
Adds the backend root to sys.path so `app.*` imports resolve correctly
regardless of where pytest is invoked from.
"""

import sys
from pathlib import Path

# This file lives at  backend/tests/conftest.py
# We need   backend/   on sys.path so  `from app.main import app`  works.
BACKEND_ROOT = Path(__file__).resolve().parent.parent  # → .../backend/
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="session")
def client():
    """Synchronous test client (no real browser launched)."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def safe_url():
    return "https://example.com"


@pytest.fixture
def private_url():
    return "http://192.168.1.1"


@pytest.fixture
def localhost_url():
    return "http://127.0.0.1:8080"