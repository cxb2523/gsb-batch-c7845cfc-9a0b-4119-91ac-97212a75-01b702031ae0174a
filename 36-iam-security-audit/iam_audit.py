# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
IAM audit module: fetches the project IAM policy and prints a
role -> members matrix, flagging primitive roles (owner/editor/viewer)
and public principals (allUsers / allAuthenticatedUsers).
"""

import os

from google.api_core import exceptions
from google.cloud import resourcemanager_v3

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

# Primitive (basic) roles predate IAM and ignore least-privilege entirely:
# editor grants write on almost every service; owner adds IAM/billing control.
# They're also what the default compute/App Engine service accounts get,
# which is the single most common GCP misconfiguration in the wild.
PRIMITIVE_ROLES = {"roles/owner", "roles/editor", "roles/viewer"}
PUBLIC_MEMBERS = {"allUsers", "allAuthenticatedUsers"}


def audit_iam_policy(project_id: str = PROJECT_ID) -> list[str]:
    """
    Fetches the project IAM policy, prints role -> members matrix,
    and returns a list of risk finding strings.

    Flags:
      - Primitive roles (owner / editor / viewer)
      - Public principals (allUsers / allAuthenticatedUsers)
      - Service accounts bound to primitive roles
    """
    client = resourcemanager_v3.ProjectsClient()
    resource = f"projects/{project_id}"

    print("=" * 80)
    print(f"IAM POLICY AUDIT — {resource}")
    print("=" * 80)

    try:
        policy = client.get_iam_policy(request={"resource": resource})
    except exceptions.PermissionDenied:
        print("Permission denied: needs resourcemanager.projects.getIamPolicy,")
        print("e.g. roles/iam.securityReviewer or roles/viewer on the project.")
        return []

    findings = []
    total_members = 0

    print(f"\n{'ROLE':<45} MEMBERS")
    print("-" * 80)
    for binding in sorted(policy.bindings, key=lambda b: b.role):
        members = list(binding.members)
        total_members += len(members)
        marker = ""
        if binding.role in PRIMITIVE_ROLES:
            marker = "  [PRIMITIVE ROLE]"
            findings.append(
                f"Primitive role {binding.role} -> {len(members)} member(s). "
                "Replace with predefined/custom least-privilege roles."
            )
        print(f"{binding.role:<45}{marker}")
        for m in members:
            flag = ""
            if m in PUBLIC_MEMBERS:
                flag = "  <-- PUBLIC ACCESS RISK"
                findings.append(
                    f"{m} bound to {binding.role}: project-level public access. "
                    "Almost never intentional — remove or scope to a resource."
                )
            # Service accounts with broad roles are the lateral-movement path:
            # anyone who can deploy code as that SA inherits the role.
            elif m.startswith("serviceAccount:") and binding.role in PRIMITIVE_ROLES:
                flag = "  <-- SA with primitive role"
            print(f"    {m}{flag}")

    print("\n" + "=" * 80)
    print(f"Bindings: {len(policy.bindings)} | member entries: {total_members}")
    if findings:
        print(f"\nRISK FINDINGS ({len(findings)}):")
        for i, f in enumerate(findings, 1):
            print(f"  {i}. {f}")
    else:
        print("\nNo primitive-role or public-access findings at project level.")
        print("(Resource-level policies — buckets, BQ, Cloud Run — need separate audits.)")

    return findings
