"""Shared test fixtures.

Tests run without Google credentials: the settlement logic in
``services.sync_service`` takes an injected ``list_files_fn`` so fake account
listings and simulated failures can be used.
"""

import os
import sqlite3
import sys
import tempfile

import bcrypt
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Make ``backend/`` importable regardless of where pytest is invoked from.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


def _seed_config_db() -> str:
    """Create a throwaway database holding the secrets ``config.py`` requires.

    Must run before ``config`` / ``database`` are imported: ``config.py`` reads
    ``DB_PATH`` at import time, and ``database.py`` binds its engine then too.
    """
    tmp_dir = tempfile.mkdtemp(prefix="drivepool-tests-")
    db_path = os.path.join(tmp_dir, "drivepool.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO app_config (key, value) VALUES (?, ?)",
            [
                ("dashboard_pin_hash", bcrypt.hashpw(b"1234", bcrypt.gensalt()).decode()),
                ("jwt_secret", "test-jwt-secret"),
                ("encryption_key", Fernet.generate_key().decode()),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


os.environ.setdefault("DB_PATH", _seed_config_db())

from database import Base  # noqa: E402
from models.models import DriveAccount, File  # noqa: E402

# ``database.py`` hardcodes its sqlite path, so importing the app would touch
# the developer's real ``backend/drivepool.db``.  Rebind the shared engine and
# session factory to a throwaway file before any app/TestClient import uses it.
import database  # noqa: E402

_TEST_DB_PATH = os.path.join(os.path.dirname(os.environ["DB_PATH"]), "app.db")
_TEST_ENGINE = create_engine(
    f"sqlite:///{_TEST_DB_PATH}", connect_args={"check_same_thread": False}
)
_TEST_SESSION = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)
database.engine = _TEST_ENGINE
database.SessionLocal = _TEST_SESSION


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def add_account(db_session):
    def _add(account_index: int, email: str | None = None, connected: bool = True):
        account = DriveAccount(
            account_index=account_index,
            email=email or f"user{account_index}@example.com",
            refresh_token="encrypted-refresh-token",
            is_connected=connected,
        )
        db_session.add(account)
        db_session.commit()
        return account

    return _add


@pytest.fixture()
def add_file(db_session):
    def _add(account_index: int, drive_file_id: str, name: str = "file.bin", size: int = 10):
        row = File(
            file_name=name,
            drive_file_id=drive_file_id,
            account_index=account_index,
            size=size,
            mime_type="application/octet-stream",
        )
        db_session.add(row)
        db_session.commit()
        return row

    return _add


def drive_file(file_id: str, name: str = "file.bin", size: int = 10):
    return {
        "id": file_id,
        "name": name,
        "size": str(size),
        "mimeType": "application/octet-stream",
        "createdTime": "2026-01-02T03:04:05.000Z",
        "parents": ["root"],
    }
