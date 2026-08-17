"""Shared fixtures.

The PIMS fixtures point the app at a throwaway SQLite file per test session and
seed it, so the suite never touches a developer's working database.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def pims_db_path() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pims-tests-"))
    return tmp / "pims.db"


@pytest.fixture(scope="session", autouse=True)
def _pims_environment(pims_db_path: Path):
    """Set the environment before ``pims.app`` reads it at import time."""

    os.environ["PIMS_DATABASE_URL"] = f"sqlite:///{pims_db_path}"
    os.environ["PIMS_ENV"] = "TEST"
    os.environ["PIMS_AUTO_SEED"] = "true"
    yield


@pytest.fixture(scope="session")
def pims_app(_pims_environment):
    from pims import db
    from pims.app import app

    db.init_db()
    return app


@pytest.fixture(scope="session")
def client(pims_app):
    from fastapi.testclient import TestClient

    with TestClient(pims_app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def admin_token(client) -> str:
    from pims.seed import DEMO_PASSWORD

    response = client.post(
        "/api/auth/login", json={"username": "cchiodo", "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


@pytest.fixture(scope="session")
def auth(admin_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def admin_user(_pims_environment) -> dict:
    from pims import db, security

    db.init_db()
    return security.get_user("cchiodo")


@pytest.fixture()
def conn(_pims_environment):
    from pims import db

    db.init_db()
    return db.get_connection()
