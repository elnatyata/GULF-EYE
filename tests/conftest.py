"""Each test session gets its own empty var/ directory (database, uploads, secret), seeded on startup."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["GULFEYE_VAR"] = tempfile.mkdtemp(prefix="gulfeye-test-")
os.environ.pop("ANTHROPIC_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend import config  # noqa: E402
from backend.app import app  # noqa: E402

PASSWORD = config.DEMO_PASSWORD


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return _login(client, "admin@gulfeye.demo")


@pytest.fixture(scope="session")
def analyst(client):
    return _login(client, "analyst@nimasa.demo")


@pytest.fixture(scope="session")
def navy(client):
    return _login(client, "ops@navy.demo")


@pytest.fixture(scope="session")
def police(client):
    return _login(client, "ops@police.demo")


@pytest.fixture(scope="session")
def viewer(client):
    return _login(client, "liaison@npa.demo")
