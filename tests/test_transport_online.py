"""
Tests for the online collection flow in ``src.transport.HttpTransport``.

These tests exercise the settlement/reporting contract, checkpoint semantics,
domain-scoped rate limiting/retries and concurrency safety without touching the
network or akshare.
"""

import logging
import os
import threading

import pandas as pd
import pytest

from src.config_models import (
    DomainRateLimitConfig,
    InterfacesConfig,
    ProjectConfigs,
    RateLimitsConfig,
    SplitConfig,
)
from src.transport import FetchReport, HttpTransport


# ----------------------------------------------------------------------------
# Fixtures / helpers
# ----------------------------------------------------------------------------
def _make_configs(domains):
    return ProjectConfigs(
        interfaces=InterfacesConfig(interfaces=[]),
        rate_limits=RateLimitsConfig(domains=domains),
        split=SplitConfig(
            train_start="2020-01-01",
            train_end="2020-12-31",
            validation_start="2021-01-01",
            validation_end="2021-06-30",
            test_start="2021-07-01",
            test_end="2021-12-31",
        ),
        features_schema={},
    )


@pytest.fixture
def domains():
    return [
        DomainRateLimitConfig(domain="default", concurrency=8, rate=10000.0, capacity=10000, retry=2),
        DomainRateLimitConfig(domain="eastmoney.com", concurrency=8, rate=10000.0, capacity=10000, retry=4),
    ]


@pytest.fixture
def transport(tmp_path, monkeypatch, domains):
    monkeypatch.setenv("BRD_USERNAME_BASE", "user")
    monkeypatch.setenv("BRD_PASSWORD", "pass")
    manifest_dir = tmp_path / "manifests"
    raw_dir = tmp_path / "raw"
    manifest_dir.mkdir()
    raw_dir.mkdir()
    return HttpTransport(
        _make_configs(domains),
        manifest_dir=str(manifest_dir),
        raw_data_dir=str(raw_dir),
        ak=None,
    )


def _task(task_id, interface_id="iface_a", source_domain="default", params=None, tmp_path=None):
    output_dir = tmp_path / f"source_domain={source_domain}" / f"interface={interface_id}"
    return {
        "task_id": task_id,
        "interface_id": interface_id,
        "source_domain": source_domain,
        "params": params or {},
        "output_path": str(output_dir / f"part-{task_id[:10]}.parquet"),
    }


def _manifest(tasks):
    return pd.DataFrame(tasks)


def _checkpoint_ids(transport):
    df = pd.read_parquet(transport.checkpoint_path)
    return list(df["task_id"])


class _RecordingBucket:
    """Stand-in token bucket that records how many tokens each domain consumed."""

    def __init__(self, name):
        self.name = name
        self.consumed = 0
        self._lock = threading.Lock()

    def consume(self, tokens=1):
        with self._lock:
            self.consumed += tokens


# ----------------------------------------------------------------------------
# A1: success writes checkpoint + success settlement
# ----------------------------------------------------------------------------
def test_success_settles_and_checkpoints(transport, tmp_path, monkeypatch):
    task = _task("a" * 64, source_domain="eastmoney.com", tmp_path=tmp_path)
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1, 2, 3]}),
    )

    report = transport.fetch(_manifest([task]))

    assert isinstance(report, FetchReport)
    assert len(report) == 1
    record = report[0]
    assert record["task_id"] == "a" * 64
    assert record["interface_id"] == "iface_a"
    assert record["source_domain"] == "eastmoney.com"
    assert record["status"] == "success"
    assert record["reason"]
    assert report.summary == {"total": 1, "success": 1, "failed": 0, "skipped": 0}

    assert os.path.exists(task["output_path"])
    assert _checkpoint_ids(transport) == ["a" * 64]

    # Successful records remain usable as downstream fetch results.
    assert report.successes[0]["raw_file_path"] == task["output_path"]
    assert report.fetch_results[0]["task"]["task_id"] == "a" * 64


# ----------------------------------------------------------------------------
# A2: failed task -> failed settlement, not checkpointed
# ----------------------------------------------------------------------------
def test_exhausted_retries_settle_failed_without_checkpoint(transport, tmp_path, monkeypatch, caplog):
    task = _task("b" * 64, tmp_path=tmp_path)

    def always_fails(interface_id, params, source_domain):
        transport._set_last_error("boom: upstream returned HTTP 500", attempts=2)
        return pd.DataFrame()

    monkeypatch.setattr(transport, "_fetch_one", always_fails)

    with caplog.at_level(logging.ERROR, logger="src.transport"):
        report = transport.fetch(_manifest([task]))

    record = report[0]
    assert record["status"] == "failed"
    assert "HTTP 500" in record["reason"]
    assert not os.path.exists(transport.checkpoint_path) or _checkpoint_ids(transport) == []
    assert not os.path.exists(task["output_path"])


def test_failure_without_explicit_reason_still_readable(transport, tmp_path, monkeypatch):
    task = _task("e" * 64, tmp_path=tmp_path)
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame(),
    )

    report = transport.fetch(_manifest([task]))

    assert report[0]["status"] == "failed"
    assert "empty dataset" in report[0]["reason"]


def test_missing_akshare_function_settles_failed(transport, tmp_path):
    """A real _fetch_one path (unknown akshare function) yields a failed settlement."""
    task = _task("f" * 64, interface_id="does_not_exist", tmp_path=tmp_path)
    transport.ak = object()  # no attribute named 'does_not_exist'

    report = transport.fetch(_manifest([task]))

    assert report[0]["status"] == "failed"
    assert "does_not_exist" in report[0]["reason"]
    assert not os.path.exists(transport.checkpoint_path)


def test_missing_akshare_library_settles_failed(transport, tmp_path):
    task = _task("0" * 64, tmp_path=tmp_path)
    transport.ak = None

    report = transport.fetch(_manifest([task]))

    assert report[0]["status"] == "failed"
    assert "akshare" in report[0]["reason"]
    assert not os.path.exists(task["output_path"])


# ----------------------------------------------------------------------------
# A3: already-checkpointed tasks are skipped
# ----------------------------------------------------------------------------
def test_checkpointed_tasks_are_skipped_on_rerun(transport, tmp_path, monkeypatch):
    good = _task("c" * 64, tmp_path=tmp_path)
    bad = _task("d" * 64, tmp_path=tmp_path)
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )

    first = transport.fetch(_manifest([good]))
    assert first[0]["status"] == "success"

    calls = []
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: calls.append(interface_id) or pd.DataFrame({"x": [1]}),
    )

    second = transport.fetch(_manifest([good, bad]))

    assert [r["task_id"] for r in second] == ["c" * 64, "d" * 64]
    assert second[0]["status"] == "skipped"
    assert second[1]["status"] == "success"
    # Only the pending task hit the network.
    assert calls == ["iface_a"]
    assert sorted(_checkpoint_ids(transport)) == ["c" * 64, "d" * 64]


def test_all_tasks_skipped_returns_report(transport, tmp_path, monkeypatch):
    task = _task("9" * 64, tmp_path=tmp_path)
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )
    transport.fetch(_manifest([task]))

    called = []
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda *a, **k: called.append(1) or pd.DataFrame({"x": [1]}),
    )
    report = transport.fetch(_manifest([task]))

    assert report.summary["skipped"] == 1
    assert called == []


# ----------------------------------------------------------------------------
# A4: domain-scoped rate limiting and retries
# ----------------------------------------------------------------------------
def test_rate_limiter_selected_by_source_domain_with_default_fallback(
    transport, tmp_path, monkeypatch
):
    recorded = {
        "eastmoney.com": _RecordingBucket("eastmoney.com"),
        "default": _RecordingBucket("default"),
    }
    transport.rate_limiters = recorded
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )

    known = _task("1" * 64, source_domain="eastmoney.com", tmp_path=tmp_path)
    unknown = _task("2" * 64, source_domain="unconfigured.example", tmp_path=tmp_path)
    no_domain = _task("3" * 64, source_domain=None, tmp_path=tmp_path)

    report = transport.fetch(_manifest([known, unknown, no_domain]))

    assert [r["status"] for r in report] == ["success"] * 3
    assert recorded["eastmoney.com"].consumed == 1
    assert recorded["default"].consumed == 2
    assert all(r["source_domain"] == "default" for r in report if r["task_id"][:1] == "3")


@pytest.mark.parametrize(
    "source_domain, expected_retries",
    [("eastmoney.com", 4), ("unconfigured.example", 2)],
)
def test_retry_budget_is_read_per_domain(transport, tmp_path, source_domain, expected_retries):
    assert transport._max_tries_for(source_domain) == expected_retries


def test_retry_loop_uses_domain_budget_and_reports_reason(transport, tmp_path):
    """The real retry loop honours the domain's retry count and records failures."""
    transport.retry_sleep_base = 0.0
    attempts = []

    class _FakeAk:
        @staticmethod
        def iface_a(**params):
            attempts.append(params)
            raise RuntimeError("upstream exploded")

    transport.ak = _FakeAk()
    task = _task("4" * 64, source_domain="eastmoney.com", tmp_path=tmp_path)

    report = transport.fetch(_manifest([task]))

    # eastmoney.com is configured with retry=4.
    assert len(attempts) == 4
    assert report[0]["status"] == "failed"
    assert "upstream exploded" in report[0]["reason"]
    assert "4 attempt" in report[0]["reason"]
    assert not os.path.exists(transport.checkpoint_path)


def test_retry_loop_success_after_transient_failures(transport, tmp_path):
    transport.retry_sleep_base = 0.0
    calls = {"n": 0}

    class _FakeAk:
        @staticmethod
        def iface_a(**params):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return pd.DataFrame({"x": [1, 2]})

    transport.ak = _FakeAk()
    task = _task("6" * 64, source_domain="eastmoney.com", tmp_path=tmp_path)

    report = transport.fetch(_manifest([task]))

    assert calls["n"] == 3
    assert report[0]["status"] == "success"
    assert _checkpoint_ids(transport) == ["6" * 64]


def test_failed_save_is_settled_failed_and_not_checkpointed(transport, tmp_path, monkeypatch):
    """If writing the raw file raises, the task must not be checkpointed."""
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )

    def broken_save(task, df):
        raise OSError("disk full")

    monkeypatch.setattr(transport, "_save_and_checkpoint", broken_save)
    task = _task("3" * 64, tmp_path=tmp_path)

    report = transport.fetch(_manifest([task]))

    assert report[0]["status"] == "failed"
    assert "disk full" in report[0]["reason"]
    assert not os.path.exists(transport.checkpoint_path)


def test_concurrency_uses_default_domain(transport):
    default_config = transport._get_domain_config("default")
    assert default_config.concurrency == 8


# ----------------------------------------------------------------------------
# A5: concurrent checkpoint writes are lossless and duplicate-free
# ----------------------------------------------------------------------------
def test_concurrent_successes_produce_complete_checkpoint(transport, tmp_path, monkeypatch):
    tasks = [_task(f"{i:064x}", tmp_path=tmp_path) for i in range(60)]
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )

    report = transport.fetch(_manifest(tasks))

    assert report.summary == {"total": 60, "success": 60, "failed": 0, "skipped": 0}
    ids = _checkpoint_ids(transport)
    assert len(ids) == 60
    assert len(set(ids)) == 60
    assert set(ids) == {t["task_id"] for t in tasks}


def test_duplicate_task_ids_in_manifest_are_not_reprocessed(transport, tmp_path, monkeypatch):
    calls = []

    def fetch_once(interface_id, params, source_domain):
        calls.append(interface_id)
        return pd.DataFrame({"x": [1]})

    monkeypatch.setattr(transport, "_fetch_one", fetch_once)
    task = _task("7" * 64, tmp_path=tmp_path)

    report = transport.fetch(_manifest([task, dict(task)]))

    assert len(calls) == 1
    assert [r["status"] for r in report] == ["success", "skipped"]
    assert _checkpoint_ids(transport) == ["7" * 64]


def test_checkpoint_merges_updates_from_other_writers(transport, tmp_path, monkeypatch):
    """A record written by another process mid-fetch must not be lost."""
    other = HttpTransport(
        transport.configs,
        manifest_dir=os.path.dirname(transport.checkpoint_path),
        raw_data_dir=transport.raw_data_dir,
        ak=None,
    )
    other_task = _task("a1" * 32, tmp_path=tmp_path)

    def fetch_with_external_write(interface_id, params, source_domain):
        # Simulate a second transport checkpointing while we are fetching.
        other._completed_task_ids = {other_task["task_id"]}
        other._last_checkpoint_signature = None
        with other.checkpoint_lock:
            other._write_checkpoint_locked()
        return pd.DataFrame({"x": [1]})

    monkeypatch.setattr(transport, "_fetch_one", fetch_with_external_write)
    our_task = _task("b2" * 32, tmp_path=tmp_path)

    report = transport.fetch(_manifest([our_task]))

    assert report[0]["status"] == "success"
    ids = _checkpoint_ids(transport)
    assert len(ids) == len(set(ids))
    assert set(ids) == {other_task["task_id"], our_task["task_id"]}


def test_missing_task_id_settles_failed(transport, tmp_path, monkeypatch):
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )
    task = _task("", tmp_path=tmp_path)

    report = transport.fetch(_manifest([task, _task("0" * 63 + "1", tmp_path=tmp_path)]))

    assert report[0]["status"] == "failed"
    assert "task_id" in report[0]["reason"]
    assert report[1]["status"] == "success"
    assert _checkpoint_ids(transport) == ["0" * 63 + "1"]


# ----------------------------------------------------------------------------
# A6: corrupted checkpoint -> warning, continue from empty
# ----------------------------------------------------------------------------
def test_corrupted_checkpoint_warns_and_continues(transport, tmp_path, monkeypatch, caplog):
    with open(transport.checkpoint_path, "wb") as f:
        f.write(b"this is certainly not parquet")

    task = _task("8" * 64, tmp_path=tmp_path)
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )

    with caplog.at_level(logging.WARNING, logger="src.transport"):
        report = transport.fetch(_manifest([task]))

    assert any("corrupt" in rec.message.lower() for rec in caplog.records)
    assert report[0]["status"] == "success"
    assert _checkpoint_ids(transport) == ["8" * 64]


def test_checkpoint_recovery_keeps_prior_records(transport, tmp_path, monkeypatch):
    """After recovering from a corrupted file, subsequent runs keep working."""
    with open(transport.checkpoint_path, "wb") as f:
        f.write(b"garbage")

    task = _task("5" * 64, tmp_path=tmp_path)
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )
    transport.fetch(_manifest([task]))

    report = transport.fetch(_manifest([task]))
    assert report[0]["status"] == "skipped"


# ----------------------------------------------------------------------------
# Report shape
# ----------------------------------------------------------------------------
def test_report_requires_all_settlement_fields(transport, tmp_path, monkeypatch):
    monkeypatch.setattr(
        transport,
        "_fetch_one",
        lambda interface_id, params, source_domain: pd.DataFrame({"x": [1]}),
    )
    tasks = [
        _task("a" * 64, source_domain="eastmoney.com", tmp_path=tmp_path),
        _task("b" * 64, source_domain="sina", tmp_path=tmp_path),
    ]
    report = transport.fetch(_manifest(tasks))

    df = report.to_dataframe()
    assert list(df.columns) == ["task_id", "interface_id", "source_domain", "status", "reason"]
    assert len(df) == 2
    assert set(df["status"]) == {"success"}
    assert df["reason"].map(bool).all()
