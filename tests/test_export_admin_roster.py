"""Offline tests for the resumable administrator-roster export.

No Telegram credentials or network access are used: the client and group entity
are replaced with local fakes.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import run


class FakeUser:
    def __init__(self, user_id, username=None, first_name=None, last_name=None, bot=False):
        self.id = user_id
        self.username = username
        self.first_name = first_name
        self.last_name = last_name
        self.bot = bot


class BrokenUser(FakeUser):
    """Simulates a participant whose details cannot be fetched (privacy restriction)."""

    @property
    def username(self):
        raise RuntimeError("UserPrivacyRestrictedError: privacy restriction")

    @username.setter
    def username(self, value):
        pass


class FakeClient:
    def __init__(self, participants, observe=None):
        self.participants = participants
        self.observe = observe
        self.disconnected = False

    def iter_participants(self, entity, filter=None):
        for index, user in enumerate(self.participants, 1):
            if self.observe is not None:
                self.observe(index)
            yield user

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def users_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "users.json"
    monkeypatch.setattr(run, "USERS_FILE", path)
    monkeypatch.setattr(run, "EXPORT_BATCH_SIZE", 100)
    return path


def install_fake_client(monkeypatch, participants, client_holder=None, observe=None):
    client = FakeClient(participants, observe=observe)
    monkeypatch.setattr(run, "with_client", lambda: client)
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))
    if client_holder is not None:
        client_holder.append(client)
    return client


def read_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_paginated_export_writes_after_each_batch(users_file, monkeypatch):
    participants = [FakeUser(i, username=f"admin{i}") for i in range(1, 251)]
    snapshots: list[tuple[int, int]] = []

    def observe(index: int) -> None:
        # Called right before the iterator yields participant `index`.
        if index > 1 and users_file.exists():
            payload = read_payload(users_file)
            snapshots.append((index, len(payload["users"])))

    install_fake_client(monkeypatch, participants, observe=observe)
    run.export_admin_roster()

    payload = read_payload(users_file)
    assert payload["status"] == "completed"
    assert payload["completed"] is True
    assert payload["progress"] == {"collected": 250, "batch": 3, "completed": True}
    assert payload["failed"] == 0
    assert [record["user_id"] for record in payload["users"]] == list(range(1, 251))
    assert payload["exported_at"].endswith("+00:00")

    # A partial file was already on disk before the later batches were fetched.
    assert any(count == 100 for _, count in snapshots)
    assert max(count for _, count in snapshots) == 200


def test_single_account_failure_is_skipped_and_reported(users_file, monkeypatch):
    participants = [
        FakeUser(1, username="ok1"),
        BrokenUser(2, username="broken"),
        FakeUser(3, username="ok3"),
    ]
    install_fake_client(monkeypatch, participants)
    run.export_admin_roster()

    payload = read_payload(users_file)
    assert payload["status"] == "completed"
    assert [record["user_id"] for record in payload["users"]] == [1, 3]
    assert payload["failed"] == 1
    assert len(payload["failures"]) == 1
    failure = payload["failures"][0]
    assert failure["user_id"] == 2
    assert "privacy restriction" in failure["reason"]


def test_network_interruption_keeps_partial_results(users_file, monkeypatch):
    participants = [FakeUser(i) for i in range(1, 151)]

    class FlakyClient(FakeClient):
        def iter_participants(self, entity, filter=None):
            for user in self.participants:
                yield user
            raise ConnectionError("network dropped")

    client = FlakyClient(participants)
    monkeypatch.setattr(run, "with_client", lambda: client)
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))

    run.export_admin_roster()

    payload = read_payload(users_file)
    assert payload["status"] == "in_progress"
    assert payload["completed"] is False
    assert payload["progress"]["collected"] == 150
    assert payload["progress"]["batch"] == 2
    assert len(payload["users"]) == 150
    assert "ConnectionError" in payload["error"]
    assert "network dropped" in payload["error"]


def test_interruption_between_batches_keeps_first_batches(users_file, monkeypatch):
    class FlakyClient(FakeClient):
        def iter_participants(self, entity, filter=None):
            for user in self.participants[:150]:
                yield user
            raise TimeoutError("read timed out")

    client = FlakyClient([FakeUser(i) for i in range(1, 1001)])
    monkeypatch.setattr(run, "with_client", lambda: client)
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))

    run.export_admin_roster()

    payload = read_payload(users_file)
    assert payload["status"] == "in_progress"
    assert payload["progress"]["batch"] == 2
    assert len(payload["users"]) == 150   # two full batches persisted, no third
    assert "TimeoutError" in payload["error"]


def test_iterator_level_account_error_is_skipped(users_file, monkeypatch):
    class SemiFlakyClient(FakeClient):
        def iter_participants(self, entity, filter=None):
            yield FakeUser(1)
            raise RuntimeError("UserPrivacyRestrictedError: privacy restriction")
            yield FakeUser(3)  # pragma: no cover - unreachable; generator ends here

    client = SemiFlakyClient([])
    monkeypatch.setattr(run, "with_client", lambda: client)
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))

    run.export_admin_roster()

    payload = read_payload(users_file)
    # The per-account error is recorded.  Because the stream then stopped
    # immediately, completeness cannot be proven, so the file stays unfinished
    # and the collected administrator is preserved.
    assert payload["status"] == "in_progress"
    assert [record["user_id"] for record in payload["users"]] == [1]
    assert payload["failed"] == 1
    assert "privacy restriction" in payload["failures"][0]["reason"]


def test_realistic_paging_fetches_details_per_batch(users_file, monkeypatch):
    """Simulate Telethon paging: participants stream in pages, details fetched
    per page, and one account on page 2 raises a privacy restriction."""
    pages = [
        [FakeUser(1), FakeUser(2)],
        [BrokenUser(3), FakeUser(4)],
        [FakeUser(5), FakeUser(6)],
    ]

    class PagedClient(FakeClient):
        def __init__(self):
            super().__init__([])
            self.pages_fetched = 0
            self.files_at_fetch = []

        def iter_participants(self, entity, filter=None):
            for page in pages:
                self.pages_fetched += 1
                # Details are resolved when a page arrives, before yielding it.
                if users_file.exists():
                    self.files_at_fetch.append((self.pages_fetched, read_payload(users_file)["progress"]["collected"]))
                for user in page:
                    yield user

    client = PagedClient()
    monkeypatch.setattr(run, "with_client", lambda: client)
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))
    monkeypatch.setattr(run, "EXPORT_BATCH_SIZE", 2)

    run.export_admin_roster()

    payload = read_payload(users_file)
    assert payload["status"] == "completed"
    assert [record["user_id"] for record in payload["users"]] == [1, 2, 4, 5, 6]
    assert payload["progress"] == {"collected": 5, "batch": 3, "completed": True}
    assert payload["failed"] == 1
    assert payload["failures"][0]["user_id"] == 3
    # No page re-fetched; page 2+ saw earlier batches already on disk
    # (3 collected because account 3 failed on page 2).
    assert client.pages_fetched == 3
    assert client.files_at_fetch == [(2, 2), (3, 3)]


def test_resume_with_group_mismatch_starts_fresh(users_file, monkeypatch):
    users_file.write_text(
        json.dumps(
            {
                "group": "Different Group",
                "status": "in_progress",
                "completed": False,
                "progress": {"collected": 1, "batch": 1, "completed": False},
                "failures": [],
                "users": [
                    {"user_id": 1, "username": "admin1", "first_name": None, "last_name": None, "is_bot": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    install_fake_client(monkeypatch, [FakeUser(42, username="fresh")])
    run.export_admin_roster()

    payload = read_payload(users_file)
    assert [record["user_id"] for record in payload["users"]] == [42]
    assert payload["group"] == "Test Group"
    assert payload["status"] == "completed"


def test_telethon_privacy_error_is_skipped_but_channel_error_interrupts(users_file, monkeypatch):
    from telethon.errors import ChannelPrivateError, UserPrivacyRestrictedError

    class RaisesOnce:
        """Like Telethon's RequestIter: a failed account does not end iteration."""

        def __init__(self, error, participants):
            self.error = error
            self.participants = participants
            self.index = 0
            self.raised = False

        def __iter__(self):
            return self

        def __next__(self):
            if self.index == 1 and not self.raised:
                self.raised = True
                raise self.error
            if self.index >= len(self.participants):
                raise StopIteration
            user = self.participants[self.index]
            self.index += 1
            return user

    class PrivacyClient(FakeClient):
        def iter_participants(self, entity, filter=None):
            return RaisesOnce(UserPrivacyRestrictedError(request=None), [FakeUser(1), FakeUser(3)])

    monkeypatch.setattr(run, "with_client", lambda: PrivacyClient([]))
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))
    run.export_admin_roster()
    payload = read_payload(users_file)
    # A per-account error followed by more participants is skipped and the
    # export still completes.  The unreadable entry has no id, so it is
    # reported by its position.
    assert payload["status"] == "completed"
    assert [record["user_id"] for record in payload["users"]] == [1, 3]
    assert payload["failed"] == 1
    assert payload["failures"][0]["user_id"] is None
    assert payload["failures"][0]["position"] == 2

    users_file.unlink()

    class PrivateClient(FakeClient):
        def iter_participants(self, entity, filter=None):
            return RaisesOnce(ChannelPrivateError(request=None), [FakeUser(1), FakeUser(3)])

    monkeypatch.setattr(run, "with_client", lambda: PrivateClient([]))
    run.export_admin_roster()
    payload = read_payload(users_file)
    assert payload["status"] == "in_progress"
    assert [record["user_id"] for record in payload["users"]] == [1]
    assert "ChannelPrivateError" in payload["error"]


def test_resume_merges_by_user_id_and_marks_complete(users_file, monkeypatch):
    users_file.write_text(
        json.dumps(
            {
                "group": "Test Group",
                "exported_at": "2020-01-01T00:00:00+00:00",
                "scope": "administrators_only",
                "status": "in_progress",
                "completed": False,
                "progress": {"collected": 2, "batch": 1, "completed": False},
                "failed": 0,
                "error": "ConnectionError: network dropped",
                "failures": [],
                "users": [
                    {"user_id": 1, "username": "admin1", "first_name": "Old", "last_name": None, "is_bot": False},
                    {"user_id": 2, "username": "admin2", "first_name": None, "last_name": None, "is_bot": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    install_fake_client(
        monkeypatch,
        [FakeUser(2, username="admin2-new", first_name="New"), FakeUser(3, username="admin3")],
    )
    run.export_admin_roster()

    payload = read_payload(users_file)
    ids = [record["user_id"] for record in payload["users"]]
    assert sorted(ids) == [1, 2, 3]
    assert len(ids) == len(set(ids))
    assert payload["status"] == "completed"
    assert payload["progress"]["completed"] is True
    # Batch numbering continues from the partial run instead of restarting at 1.
    assert payload["progress"]["batch"] == 2
    assert payload["exported_at"] != "2020-01-01T00:00:00+00:00"
    by_id = {record["user_id"]: record for record in payload["users"]}
    assert by_id[2]["username"] == "admin2-new"
    assert by_id[1]["username"] == "admin1"


def test_resume_preserves_prior_failures_and_clears_them_on_success(users_file, monkeypatch):
    users_file.write_text(
        json.dumps(
            {
                "group": "Test Group",
                "exported_at": "2020-01-01T00:00:00+00:00",
                "scope": "administrators_only",
                "status": "in_progress",
                "completed": False,
                "progress": {"collected": 1, "batch": 1, "completed": False},
                "failed": 1,
                "error": "ConnectionError: network dropped",
                "failures": [{"user_id": 9, "reason": "RuntimeError: timeout", "batch": 1, "position": 2}],
                "users": [
                    {"user_id": 1, "username": "admin1", "first_name": None, "last_name": None, "is_bot": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    # Account 9 is fetched successfully this time, so its stale failure is cleared.
    install_fake_client(monkeypatch, [FakeUser(1), FakeUser(9, username="recovered")])
    run.export_admin_roster()

    payload = read_payload(users_file)
    assert sorted(record["user_id"] for record in payload["users"]) == [1, 9]
    assert payload["failed"] == 0
    assert payload["failures"] == []


def test_only_administrators_are_requested_and_totals_are_printed(users_file, monkeypatch, capsys):
    from telethon.tl.types import ChannelParticipantsAdmins

    filters = []

    class FilterCapturingClient(FakeClient):
        def iter_participants(self, entity, filter=None):
            filters.append(filter)
            return super().iter_participants(entity, filter=filter)

    client = FilterCapturingClient([FakeUser(1), BrokenUser(2)])
    monkeypatch.setattr(run, "with_client", lambda: client)
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))

    run.export_admin_roster()

    assert filters == [ChannelParticipantsAdmins]
    out = capsys.readouterr().out
    assert "1 administrator(s)" in out
    assert "1 failed" in out
    assert "user 2" in out


def test_resume_keeps_saved_record_if_account_now_fails(users_file, monkeypatch):
    users_file.write_text(
        json.dumps(
            {
                "group": "Test Group",
                "status": "in_progress",
                "completed": False,
                "progress": {"collected": 1, "batch": 1, "completed": False},
                "failures": [],
                "users": [
                    {"user_id": 2, "username": "saved", "first_name": "Saved", "last_name": None, "is_bot": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    install_fake_client(monkeypatch, [BrokenUser(2, username="broken")])
    run.export_admin_roster()

    payload = read_payload(users_file)
    # The previously collected record is not downgraded to a failure.
    assert [record["user_id"] for record in payload["users"]] == [2]
    assert payload["users"][0]["username"] == "saved"
    assert payload["failed"] == 0


def test_resume_can_be_interrupted_again(users_file, monkeypatch):
    users_file.write_text(
        json.dumps(
            {
                "group": "Test Group",
                "status": "in_progress",
                "completed": False,
                "progress": {"collected": 1, "batch": 1, "completed": False},
                "failures": [],
                "users": [
                    {"user_id": 1, "username": "admin1", "first_name": None, "last_name": None, "is_bot": False},
                ],
            }
        ),
        encoding="utf-8",
    )

    class FlakyClient(FakeClient):
        def iter_participants(self, entity, filter=None):
            yield FakeUser(1)
            yield FakeUser(2)
            raise ConnectionError("still offline")

    monkeypatch.setattr(run, "with_client", lambda: FlakyClient([]))
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))
    run.export_admin_roster()

    payload = read_payload(users_file)
    assert payload["status"] == "in_progress"
    assert sorted(record["user_id"] for record in payload["users"]) == [1, 2]
    assert "ConnectionError" in payload["error"]


def test_failed_restart_keeps_previous_completed_export(users_file, monkeypatch):
    users_file.write_text(
        json.dumps(
            {
                "group": "Test Group",
                "exported_at": "2020-01-01T00:00:00+00:00",
                "status": "completed",
                "completed": True,
                "progress": {"collected": 1, "batch": 1, "completed": True},
                "failures": [],
                "users": [
                    {"user_id": 1, "username": "admin1", "first_name": None, "last_name": None, "is_bot": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    before = users_file.read_text(encoding="utf-8")

    class DeadClient(FakeClient):
        def iter_participants(self, entity, filter=None):
            raise ConnectionError("offline")
            yield  # pragma: no cover

    monkeypatch.setattr(run, "with_client", lambda: DeadClient([]))
    monkeypatch.setattr(run, "group_entity", lambda _client: SimpleNamespace(title="Test Group"))
    run.export_admin_roster()

    assert users_file.read_text(encoding="utf-8") == before


def test_completed_export_starts_fresh(users_file, monkeypatch):
    users_file.write_text(
        json.dumps(
            {
                "group": "Old Group",
                "exported_at": "2020-01-01T00:00:00+00:00",
                "scope": "administrators_only",
                "status": "completed",
                "completed": True,
                "progress": {"collected": 1, "batch": 1, "completed": True},
                "failed": 0,
                "error": None,
                "failures": [],
                "users": [
                    {"user_id": 1, "username": "admin1", "first_name": None, "last_name": None, "is_bot": False},
                ],
            }
        ),
        encoding="utf-8",
    )
    install_fake_client(monkeypatch, [FakeUser(42, username="fresh")])
    run.export_admin_roster()

    payload = read_payload(users_file)
    assert [record["user_id"] for record in payload["users"]] == [42]
    assert payload["status"] == "completed"
