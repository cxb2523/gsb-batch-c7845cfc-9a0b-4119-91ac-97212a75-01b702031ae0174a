# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Service account key audit module: lists all service accounts in a project
plus their user-managed keys with age in days; flags keys older than 90 days.
Exported JSON keys are long-lived bearer credentials — the #1 GCP
credential-leak vector.
"""

import os
from datetime import datetime, timezone

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
KEY_AGE_THRESHOLD_DAYS = 90  # common compliance bar (CIS GCP 1.7 uses 90 days)


def audit_sa_keys(project_id: str = PROJECT_ID, threshold_days: int = KEY_AGE_THRESHOLD_DAYS) -> dict:
    """
    Lists all service accounts and their user-managed keys.
    Flags keys older than threshold_days.

    Returns a dict with:
      - 'total_accounts': int
      - 'total_user_keys': int
      - 'stale_keys': int
    """
    # Governance context: user-managed keys never expire on their own and work
    # from anywhere — no VPC, no device, no MFA. Prefer in order:
    #   1. Attached service accounts (workload identity on GKE/Cloud Run/GCE)
    #   2. Workload Identity Federation for external systems (no keys at all)
    #   3. Short-lived tokens via impersonation
    # Keys should be the last resort, and org policy
    # iam.disableServiceAccountKeyCreation should block new ones by default.
    credentials, _ = google.auth.default()
    service = build("iam", "v1", credentials=credentials)
    now = datetime.now(timezone.utc)

    print("=" * 80)
    print(f"SERVICE ACCOUNT + KEY AUDIT — projects/{project_id}")
    print("=" * 80)

    try:
        accounts = []
        request = service.projects().serviceAccounts().list(
            name=f"projects/{project_id}", pageSize=100
        )
        while request is not None:
            response = request.execute()
            accounts.extend(response.get("accounts", []))
            request = service.projects().serviceAccounts().list_next(request, response)
    except HttpError as e:
        if e.resp.status == 403:
            print("Permission denied: needs iam.serviceAccounts.list, e.g.")
            print("roles/iam.securityReviewer or roles/viewer on the project.")
            return {"total_accounts": 0, "total_user_keys": 0, "stale_keys": 0}
        raise

    stale_keys = 0
    total_user_keys = 0
    for sa in accounts:
        email = sa["email"]
        disabled = " [DISABLED]" if sa.get("disabled") else ""
        print(f"\n  {email}{disabled}")
        print(f"    display: {sa.get('displayName', '(none)')}")
        try:
            keys = (
                service.projects().serviceAccounts().keys()
                .list(name=sa["name"], keyTypes="USER_MANAGED")
                .execute()
                .get("keys", [])
            )
        except HttpError as e:
            print(f"    (cannot list keys: HTTP {e.resp.status} — needs "
                  f"iam.serviceAccountKeys.list)")
            continue

        if not keys:
            print("    user-managed keys: none (good — attached/federated auth only)")
            continue

        for key in keys:
            total_user_keys += 1
            created = datetime.fromisoformat(
                key["validAfterTime"].replace("Z", "+00:00")
            )
            age_days = (now - created).days
            flag = ""
            if age_days > threshold_days:
                stale_keys += 1
                flag = f"  <-- STALE (> {threshold_days}d — rotate or delete)"
            key_id = key["name"].rsplit("/", 1)[-1]
            print(f"    key {key_id[:12]}...  age={age_days}d{flag}")

    print("\n" + "=" * 80)
    print(f"Service accounts: {len(accounts)} | user-managed keys: {total_user_keys} "
          f"| stale (> {threshold_days}d): {stale_keys}")
    if stale_keys:
        print("ACTION: rotate stale keys, then migrate the workload off keys entirely.")

    return {
        "total_accounts": len(accounts),
        "total_user_keys": total_user_keys,
        "stale_keys": stale_keys,
    }
