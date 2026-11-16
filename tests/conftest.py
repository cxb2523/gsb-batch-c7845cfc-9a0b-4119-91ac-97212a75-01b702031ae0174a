"""Shared fixtures for TickVault tests."""

import os
import tempfile

# Redirect the import-time log file / data directory away from the repo
# before any tick_vault module is imported.
os.environ.setdefault(
    "TICK_VAULT_BASE_DIRECTORY",
    tempfile.mkdtemp(prefix="tick_vault_test_import_"),
)

import pytest

from tick_vault.config import CONFIG


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """
    Point the global CONFIG at a per-test temporary directory and use
    fast worker/metadata settings suitable for tests.

    All tick_vault modules hold a reference to the same CONFIG object, so
    mutating its fields here is visible everywhere.
    """
    monkeypatch.setattr(CONFIG, "base_directory", tmp_path / "data")
    monkeypatch.setattr(CONFIG, "worker_per_proxy", 2)
    monkeypatch.setattr(CONFIG, "worker_queue_timeout", 10.0)
    monkeypatch.setattr(CONFIG, "metadata_update_batch_timeout", 0.05)
    monkeypatch.setattr(CONFIG, "metadata_update_batch_size", 2)
    return CONFIG
