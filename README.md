# Telegram Group Manager

A command-line Telegram group utility built with Telethon. It supports account/session management and a menu-driven group workflow.

## Requirements
- Python 3.10+
- Telegram API ID and API hash from https://my.telegram.org
- Admin permissions for group announcements, moderation, or invite-link creation as applicable

## Installation
```bash
git clone https://github.com/saifalisew1508/Telegram-Members-Adder.git
cd Telegram-Members-Adder
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\\Scripts\\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configure credentials
Set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in your terminal environment. Do not commit them.

Linux/macOS:
```bash
export TELEGRAM_API_ID="YOUR_API_ID"
export TELEGRAM_API_HASH="YOUR_API_HASH"
```

Windows PowerShell:
```powershell
$env:TELEGRAM_API_ID="YOUR_API_ID"
$env:TELEGRAM_API_HASH="YOUR_API_HASH"
```

## 1. Account manager
Run `python manager.py` to add/login accounts, check saved sessions, or remove an account. Account metadata is stored in `accounts.json`; Telethon sessions are stored in `sessions/`.

## 2. Group manager
Run `python run.py` to open the interactive menu:

1. **Send a group announcement** - sends one message into the selected group/channel (for example, a family-group good morning message). It does not DM every member.
2. **Ban one member** - enter one username or user ID. Requires the logged-in account to have appropriate admin rights and asks for explicit confirmation.
3. **Unban one member** - enter one username or user ID and confirm.
4. **Export administrators** - saves the group/channel administrator roster to `users.json`, including user ID, username and bot flag. It does not scrape the entire membership. The roster is collected in batches and saved after every batch, so a large group no longer needs one long uninterrupted run:
   - `status`/`completed`/`progress` describe the export (`in_progress` or `completed`, collected count, batch number).
   - An administrator whose details cannot be read (for example a privacy restriction) is skipped, kept in the `failures` list with the reason, and the export continues.
   - If the network drops mid-export, the already collected administrators stay in `users.json`, the file remains `in_progress`, and a readable summary is printed. `exported_at` shows the time of the last save.
   - Running the export again while `users.json` is `in_progress` resumes it: saved `user_id` values are kept, new ones are merged in without duplicates, and the file is marked `completed` with a refreshed `exported_at`. A finished export is replaced by a fresh one on the next run.
5. **Create an invitation link** - creates a link that people can use to join voluntarily. The account needs invite-link permission.

## Data and security
- Keep `sessions/`, `accounts.json`, `users.json`, API credentials, login codes, and 2FA passwords private.
- `users.json` is local data and is excluded from Git.
- The old `vars.txt` pickle format is not loaded. Register accounts again using `manager.py`.
- Use moderation only when justified and authorized. This project deliberately does not provide mass bans/unbans, member scraping, unsolicited mass-DMs, or automatic bulk member additions.
- Respect Telegram's Terms, user privacy, and group rules.

## Troubleshooting
- Missing API credentials: set both environment variables in the same terminal session.
- Login required: run `python manager.py` and log in again.
- Permission errors: ask a group administrator to grant the required rights.
- Export stuck `in_progress`: run option 4 again to resume; the previously saved administrators are kept and merged by user ID.

## Tests
The administrator export is covered by offline tests that use fake participant iterators (no Telegram credentials or network needed):
```bash
pip install pytest
python -m pytest tests -q
```
