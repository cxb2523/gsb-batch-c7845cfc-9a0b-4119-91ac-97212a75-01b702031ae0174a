# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 36 — IAM Security Audit
Runs a full three-phase security audit:
  1. API sprawl check (Service Usage)
  2. IAM policy primitive-role / public-access check (Resource Manager)
  3. Service account key age check (IAM API)

Then prints a consolidated risk summary.
"""

import os

from api_audit import list_enabled_apis
from iam_audit import audit_iam_policy
from sa_key_audit import audit_sa_keys

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")


def print_risk_summary(api_names: list, iam_findings: list, sa_stats: dict) -> None:
    print("\n")
    print("=" * 80)
    print("CONSOLIDATED RISK SUMMARY")
    print("=" * 80)

    # --- APIs ---
    api_count = len(api_names)
    api_risk = "HIGH — review for API sprawl (>40 APIs)" if api_count > 40 else "OK"
    print(f"\n[API SPRAWL]     {api_count} APIs enabled — {api_risk}")

    # --- IAM ---
    iam_risk = f"HIGH — {len(iam_findings)} finding(s)" if iam_findings else "OK"
    print(f"[IAM POLICY]     {iam_risk}")
    for f in iam_findings:
        print(f"  * {f}")

    # --- SA Keys ---
    stale = sa_stats.get("stale_keys", 0)
    total_keys = sa_stats.get("total_user_keys", 0)
    total_accounts = sa_stats.get("total_accounts", 0)
    key_risk = f"HIGH — {stale} stale key(s) need rotation/deletion" if stale else "OK"
    print(f"[SA KEYS]        {total_accounts} SAs | {total_keys} user-managed keys | {key_risk}")

    print("\n" + "-" * 80)
    total_risks = (1 if api_count > 40 else 0) + len(iam_findings) + (1 if stale else 0)
    if total_risks == 0:
        print("No critical findings. Run this audit weekly and after any deployment.")
    else:
        print(f"ACTION REQUIRED: {total_risks} risk area(s) identified above.")
        print("Remediate in order: public access > primitive roles > stale keys > API sprawl.")
    print("=" * 80)


def main():
    print("\n")
    print("#" * 80)
    print(f"  GCP IAM SECURITY AUDIT — project: {PROJECT_ID}")
    print("#" * 80)

    # Phase 1: API sprawl
    api_names = list_enabled_apis(PROJECT_ID)

    print("\n")

    # Phase 2: IAM policy
    iam_findings = audit_iam_policy(PROJECT_ID)

    print("\n")

    # Phase 3: SA key ages
    sa_stats = audit_sa_keys(PROJECT_ID)

    # Consolidated risk summary
    print_risk_summary(api_names, iam_findings, sa_stats)


if __name__ == "__main__":
    main()
