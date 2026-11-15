# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Bucket lifecycle rules for Cloud Storage.

Lifecycle rules are the cheapest cost lever in GCS:
  - Set-and-forget policies enforced by the platform at no compute cost.
  - NEARLINE costs ~50% less per GB than STANDARD for data accessed < 1/month.
  - Auto-deletion eliminates silent accumulation of temp/scratch objects.

Rules are evaluated roughly daily — actions are not instant. Re-applying
the same rules appends duplicates; call clear_lifecycle_rules() before
re-running if needed.

Common rule types:
  Delete         — remove objects matching age/prefix/storage class conditions
  SetStorageClass — transition to NEARLINE, COLDLINE, or ARCHIVE
"""

import os

from google.cloud import storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")


def print_lifecycle_rules(bucket: storage.Bucket, label: str = "") -> None:
    """Print all lifecycle rules currently configured on the bucket."""
    rules = list(bucket.lifecycle_rules)
    header = f"Lifecycle rules on gs://{bucket.name}"
    if label:
        header += f" ({label})"
    print(f"\n--- {header} ---")
    if rules:
        for i, rule in enumerate(rules, 1):
            print(f"  Rule {i}: {rule}")
    else:
        print("  (none)")


def clear_lifecycle_rules(bucket: storage.Bucket) -> None:
    """Remove all lifecycle rules from the bucket."""
    bucket.lifecycle_rules = []
    bucket.patch()
    bucket.reload()
    print(f"Cleared all lifecycle rules from gs://{bucket.name}")


def apply_standard_lifecycle_rules(bucket: storage.Bucket) -> None:
    """
    Apply two common lifecycle rules:

    Rule 1 — Delete scratch objects under 'tmp/' after 7 days.
             Prevents silent accumulation of temporary data.

    Rule 2 — Transition STANDARD objects to NEARLINE after 30 days.
             ~50% storage cost reduction for infrequently-accessed data.
             Minimum storage duration for NEARLINE is 30 days — only apply
             to data you're confident won't be deleted before 30 days.
    """
    # Rule 1: auto-delete temp objects
    bucket.add_lifecycle_delete_rule(
        age=7,
        matches_prefix=["tmp/", "scratch/", "demo/"],
    )

    # Rule 2: downgrade STANDARD to NEARLINE after 30 days
    bucket.add_lifecycle_set_storage_class_rule(
        storage_class="NEARLINE",
        age=30,
        matches_storage_class=["STANDARD"],
    )

    bucket.patch()
    bucket.reload()
    print(f"\nApplied 2 lifecycle rules to gs://{bucket.name}")
    print_lifecycle_rules(bucket, label="after")

    print("\nFinOps tip:")
    print("  NEARLINE   : ~50% cheaper than STANDARD; access fee applies; min 30-day storage")
    print("  COLDLINE   : ~80% cheaper; higher access fee; min 90-day storage")
    print("  ARCHIVE    : ~94% cheaper; highest access fee; min 365-day storage")
    print("  Rule evaluation is daily — not instant.")


def apply_custom_delete_rule(bucket: storage.Bucket, prefix: str, age_days: int) -> None:
    """Apply a single delete rule for a specific prefix and age."""
    bucket.add_lifecycle_delete_rule(age=age_days, matches_prefix=[prefix])
    bucket.patch()
    bucket.reload()
    print(f"\nAdded delete rule: prefix='{prefix}' age={age_days}d on gs://{bucket.name}")
    print_lifecycle_rules(bucket, label="updated")
