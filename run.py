"""Telegram group utility.

Features are intentionally limited to group announcements, individual moderation,
admin-roster export, and opt-in invitation links.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from colorama import Fore, init
from telethon.errors import (
    AuthKeyError,
    ChannelInvalidError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodError,
    ServerError,
    TimedOutError as RpcTimedOutError,
)
from telethon.sync import TelegramClient
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import ChatBannedRights, ChannelParticipantsAdmins

init(autoreset=True)
ROOT = Path(__file__).resolve().parent
SESSIONS = ROOT / "sessions"
ACCOUNTS_FILE = ROOT / "accounts.json"
USERS_FILE = ROOT / "users.json"
EXPORT_BATCH_SIZE = 100
# Safety valve: if the participant stream raises this many times in a row without
# producing anyone, treat it as broken and stop instead of looping forever.
MAX_CONSECUTIVE_SKIPS = 25


def credentials() -> tuple[int, str]:
    api_id, api_hash = os.getenv("TELEGRAM_API_ID", "").strip(), os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id.isdigit() or not api_hash:
        raise RuntimeError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables.")
    return int(api_id), api_hash


def accounts() -> list[str]:
    if not ACCOUNTS_FILE.exists():
        return []
    data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("accounts.json must contain a JSON array.")
    return [x["phone"] for x in data if isinstance(x, dict) and isinstance(x.get("phone"), str)]


def choose_account() -> str:
    saved = accounts()
    if not saved:
        raise RuntimeError("No accounts saved. Run python manager.py first.")
    for i, phone in enumerate(saved, 1):
        print(f"{i}. {phone}")
    choice = input("Select account: ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(saved):
        raise ValueError("Invalid account selection.")
    return saved[int(choice) - 1]


def with_client():
    api_id, api_hash = credentials()
    phone = choose_account()
    SESSIONS.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(SESSIONS / re.sub(r"[^0-9+]", "", phone)), api_id, api_hash)
    client.start(phone=phone)
    return client


def group_entity(client):
    group = input("Group/channel username, link, or ID: ").strip()
    if not group:
        raise ValueError("Group/channel is required.")
    return client.get_entity(group)


def confirm(action: str, target: str) -> bool:
    print(Fore.YELLOW + f"\nYou are about to {action}: {target}")
    return input("Type CONFIRM to continue: ").strip() == "CONFIRM"


def send_group_announcement() -> None:
    client = with_client()
    try:
        entity = group_entity(client)
        print("This sends ONE message to the group, not private messages to members.")
        message = input("Announcement text (e.g. Good morning!): ").strip()
        if not message:
            print("Message cannot be empty.")
            return
        title = getattr(entity, "title", str(entity))
        if confirm("send a group announcement in", title):
            client.send_message(entity, message)
            print(Fore.GREEN + "Announcement sent to the group.")
    finally:
        client.disconnect()


def moderate_one_member(unban: bool = False) -> None:
    client = with_client()
    try:
        entity = group_entity(client)
        user_ref = input("Target member username or numeric user ID: ").strip()
        if not user_ref:
            raise ValueError("A member username or ID is required.")
        user = client.get_entity(int(user_ref) if user_ref.lstrip("-").isdigit() else user_ref)
        label = f"{getattr(user, 'first_name', '')} (@{getattr(user, 'username', None) or 'no username'}, ID {user.id})"
        action = "unban" if unban else "ban"
        if not confirm(action, label):
            print("Cancelled.")
            return
        rights = ChatBannedRights(
            until_date=None,
            view_messages=not unban,
            send_messages=False if unban else True,
            send_media=False if unban else True,
            send_stickers=False if unban else True,
            send_gifs=False if unban else True,
            send_games=False if unban else True,
            send_inline=False if unban else True,
            embed_links=False if unban else True,
        )
        client(EditBannedRequest(entity, user, rights))
        print(Fore.GREEN + f"Member {action} completed.")
    finally:
        client.disconnect()


def _safe_user_id(user) -> object | None:
    try:
        return getattr(user, "id", None)
    except Exception:
        return None


def _failure_key(record: dict) -> tuple:
    user_id = record.get("user_id")
    if user_id is not None:
        return ("id", user_id)
    return ("anon", record.get("batch"), record.get("position"))


def _is_fatal_iteration_error(exc: BaseException) -> bool:
    """True when an error ends the whole traversal instead of one account.

    Network loss, timeouts, flood waits, session/auth failures and losing
    access to the group mean the participant stream is dead.  Anything else
    (for example a privacy restriction on one account) is skipped, with a
    consecutive-skip cap as a safety valve for an unexpectedly broken stream.
    """
    if not isinstance(exc, Exception):
        return True  # KeyboardInterrupt, SystemExit, ...
    return isinstance(
        exc,
        (
            ConnectionError,
            OSError,
            TimeoutError,
            RpcTimedOutError,
            FloodError,
            AuthKeyError,
            ServerError,
            ChannelPrivateError,
            ChannelInvalidError,
            ChatAdminRequiredError,
        ),
    )


def _load_export(path: Path) -> dict:
    """Read a previous export.

    Returns a dict with ``users``, ``failures``, ``resuming``, ``had_completed``
    and ``group``.  Only an explicitly unfinished export is resumed; a completed
    (or legacy, pre-status) export is replaced by a fresh one.  A resumed
    traversal restarts from the beginning of the admin list and merges by
    ``user_id``, so failures that could not be tied to a ``user_id`` are dropped
    instead of piling up across runs.
    """
    empty = {"users": [], "failures": [], "resuming": False, "had_completed": False, "group": None, "batch": 0}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(Fore.YELLOW + f"Could not read {path.name} ({type(exc).__name__}: {exc}); starting a fresh export.")
        return empty
    if not isinstance(data, dict):
        print(Fore.YELLOW + f"{path.name} is not a JSON object; starting a fresh export.")
        return empty

    group = data.get("group") if isinstance(data.get("group"), str) else None
    progress = data.get("progress")
    progress = progress if isinstance(progress, dict) else {}
    status = data.get("status")
    if isinstance(status, str):
        resuming = status != "completed"
    else:
        completed_flag = data.get("completed", progress.get("completed"))
        resuming = completed_flag is False

    raw_users = data.get("users")
    users = [
        record for record in raw_users
        if isinstance(record, dict) and record.get("user_id") is not None
    ] if isinstance(raw_users, list) else []
    raw_failures = data.get("failures")
    failures = [record for record in raw_failures if isinstance(record, dict)] if isinstance(raw_failures, list) else []

    if not resuming:
        return {**empty, "had_completed": bool(users) or status == "completed", "group": group}

    batch = progress.get("batch")
    failures = [record for record in failures if record.get("user_id") is not None]
    return {
        "users": users,
        "failures": failures,
        "resuming": True,
        "had_completed": False,
        "group": group,
        "batch": batch if isinstance(batch, int) and batch >= 0 else 0,
    }


def _write_export(
    path: Path,
    group: str,
    records: dict,
    failures: dict,
    batch: int,
    completed: bool,
    error: BaseException | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "group": group,
        "exported_at": now,
        "scope": "administrators_only",
        "status": "completed" if completed else "in_progress",
        "completed": completed,
        "progress": {
            "collected": len(records),
            "batch": batch,
            "completed": completed,
        },
        "failed": len(failures),
        "error": f"{type(error).__name__}: {error}" if error is not None else None,
        "failures": list(failures.values()),
        "users": list(records.values()),
    }
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _participant_record(user) -> tuple[dict | None, str | None]:
    """Build a roster record; return ``(record, failure_reason)``."""
    try:
        user_id = user.id
        if user_id is None:
            raise ValueError("participant has no user id")
        return {
            "user_id": user_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_bot": user.bot,
        }, None
    except Exception as exc:
        # One unreadable account must not abort the export.
        return None, f"{type(exc).__name__}: {exc}"


def _print_failures(failures: dict, limit: int = 5) -> None:
    """Print a short, human-readable list of accounts that could not be read."""
    if not failures:
        return
    entries = list(failures.values())
    print(Fore.YELLOW + f"Accounts that could not be read ({len(entries)}):")
    for failure in entries[:limit]:
        user_id = failure.get("user_id")
        label = f"user {user_id}" if user_id is not None else f"entry #{failure.get('position', '?')}"
        print(Fore.YELLOW + f"  - {label}: {failure.get('reason')} (batch {failure.get('batch', '?')})")
    if len(entries) > limit:
        print(Fore.YELLOW + f"  ... and {len(entries) - limit} more; see the \"failures\" list in users.json.")


def _export_into(
    target: Path,
    group: str,
    participants,
    records: dict,
    failures: dict,
    batch_size: int,
    preserve_existing: bool = False,
    batch_offset: int = 0,
) -> tuple[int, bool, BaseException | None]:
    """Collect administrators batch by batch, saving after every batch.

    Returns ``(batches_done, completed, interruption)``.

    * A participant whose details cannot be read is recorded in ``failures``
      and skipped; the traversal continues.
    * A network/timeout/auth error ends the traversal and is returned as
      ``interruption``.

    Records collected before an interruption are already written to ``target``.
    With ``preserve_existing`` an earlier completed export is left untouched if
    this run fails before collecting anything.  ``batch_offset`` continues the
    batch numbering of a resumed export.
    """
    position = 0
    batch_number = batch_offset
    completed = False
    interruption: BaseException | None = None
    consecutive_skips = 0
    pending_skip: BaseException | None = None
    seen_ids: set = set()
    iterator = iter(participants)

    while True:
        try:
            user = next(iterator)
        except StopIteration:
            completed = True
            if pending_skip is not None:
                # The stream stopped right after an error, so we cannot prove
                # the traversal finished: keep partial results, mark unfinished.
                completed = False
                interruption = pending_skip
        except BaseException as exc:
            if _is_fatal_iteration_error(exc) or consecutive_skips >= MAX_CONSECUTIVE_SKIPS:
                interruption = exc
            else:
                # A single account could not be read (privacy limit / transient
                # error): log it, skip it, and keep iterating.
                consecutive_skips += 1
                pending_skip = exc
                position += 1
                failure = {
                    "user_id": None,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "batch": batch_number + 1,
                    "position": position,
                }
                failures[_failure_key(failure)] = failure
        else:
            consecutive_skips = 0
            pending_skip = None
            position += 1
            try:
                record, reason = _participant_record(user)
            except BaseException as exc:
                # Cancellation while reading one account still ends the export.
                interruption = exc
            else:
                if record is not None:
                    seen_ids.add(record["user_id"])
                    records[record["user_id"]] = record
                    failures.pop(("id", record["user_id"]), None)
                else:
                    user_id = _safe_user_id(user)
                    if user_id is not None:
                        seen_ids.add(user_id)
                    if user_id is None or user_id not in records:
                        # An account saved by an earlier run keeps its good record.
                        failure = {
                            "user_id": user_id,
                            "reason": reason,
                            "batch": batch_number + 1,
                            "position": position,
                        }
                        failures[_failure_key(failure)] = failure

        if completed or interruption is not None or position >= batch_size:
            if position:
                batch_number += 1
                position = 0
            if completed:
                # Drop stale failures from earlier partial runs for accounts that
                # are no longer in the admin list.
                for key, failure in list(failures.items()):
                    user_id = failure.get("user_id")
                    if user_id is not None and user_id not in seen_ids:
                        failures.pop(key, None)
            # A fresh run over an earlier completed export only replaces it once
            # at least one administrator has been collected; an immediate failure
            # leaves the previous file untouched.
            if preserve_existing and not records and not completed and batch_number == 0:
                return batch_number, completed, interruption
            _write_export(
                target,
                group,
                records,
                failures,
                batch_number,
                completed,
                interruption,
            )
        if completed or interruption is not None:
            break
    return batch_number, completed, interruption


def _current_status(path: Path) -> str | None:
    """Best-effort read of the status currently stored in ``users.json``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if isinstance(status, str):
        return status
    return "completed" if data.get("completed") is True else None


def export_admin_roster() -> None:
    target = Path(USERS_FILE)
    saved = _load_export(target)
    records = {record["user_id"]: record for record in saved["users"]}
    failures = {_failure_key(record): record for record in saved["failures"]}
    batch_offset = saved["batch"]

    if saved["resuming"]:
        print(Fore.YELLOW + f"Resuming incomplete export from {target.name}: {len(records)} administrator(s) already saved.")
    elif saved["had_completed"]:
        print(Fore.YELLOW + f"{target.name} contains a completed export; starting a fresh export.")

    client = with_client()
    try:
        entity = group_entity(client)
        title = getattr(entity, "title", str(entity))
        if saved["resuming"] and saved["group"] and saved["group"] != title:
            # Never mix administrators from two different groups.
            print(Fore.YELLOW + f"{target.name} was started for \"{saved['group']}\", not \"{title}\"; starting a fresh export.")
            records, failures = {}, {}
            batch_offset = 0
        try:
            participants = client.iter_participants(entity, filter=ChannelParticipantsAdmins)
            batch, _completed, interruption = _export_into(
                target,
                title,
                participants,
                records,
                failures,
                EXPORT_BATCH_SIZE,
                preserve_existing=saved["had_completed"],
                batch_offset=batch_offset,
            )
        except BaseException as exc:
            # The traversal could not even start; still persist the state we have.
            batch, interruption = batch_offset, exc
            if not saved["had_completed"]:
                try:
                    _write_export(target, title, records, failures, batch_offset, False, exc)
                except OSError as write_exc:
                    print(Fore.RED + f"Could not save partial results to {target.name}: {write_exc}")

        if interruption is not None:
            print(Fore.RED + f"Export interrupted in batch {max(batch, 1)}: {type(interruption).__name__}: {interruption}")
            saved_status = _current_status(target)
            if saved_status == "completed":
                print(Fore.YELLOW + f"The previous completed export in {target.name} was left unchanged.")
            else:
                print(
                    Fore.YELLOW
                    + f"Partial results kept in {target.name}: {len(records)} administrator(s) saved, "
                    + f"{len(failures)} failed. Status: in_progress (run the export again to resume)."
                )
                _print_failures(failures)
            if not isinstance(interruption, Exception):
                raise interruption
        else:
            print(
                Fore.GREEN
                + f"Export complete: {len(records)} administrator(s) saved to {target.name}, "
                + f"{len(failures)} failed."
            )
            _print_failures(failures)
    finally:
        client.disconnect()


def create_invite_link() -> None:
    client = with_client()
    try:
        entity = group_entity(client)
        if not confirm("create an invitation link for", getattr(entity, "title", str(entity))):
            print("Cancelled.")
            return
        invite = client(ExportChatInviteRequest(peer=entity))
        print(Fore.GREEN + "\nInvitation link (share only with people who choose to join):")
        print(invite.link)
    finally:
        client.disconnect()


def main() -> None:
    actions = {
        "1": ("Send a group announcement (e.g. Good morning)", send_group_announcement),
        "2": ("Ban one member", lambda: moderate_one_member(False)),
        "3": ("Unban one member", lambda: moderate_one_member(True)),
        "4": ("Export group administrators to users.json", export_admin_roster),
        "5": ("Create an opt-in group invitation link", create_invite_link),
    }
    while True:
        print(Fore.CYAN + "\n===== TELEGRAM GROUP MANAGER =====")
        for key, (label, _) in actions.items():
            print(f"{key}. {label}")
        print("6. Exit")
        choice = input("Choose an option: ").strip()
        if choice == "6":
            break
        action = actions.get(choice)
        if not action:
            print(Fore.YELLOW + "Choose a number from 1 to 6.")
            continue
        try:
            action[1]()
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
        except Exception as exc:
            print(Fore.RED + f"Error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
