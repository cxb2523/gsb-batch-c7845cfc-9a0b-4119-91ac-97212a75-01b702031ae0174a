# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
API audit module: lists every enabled API/service in the project via
the Service Usage API. Each enabled API is attack surface, quota exposure,
and a hint of potentially forgotten workloads.
"""

import os

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")


def list_enabled_apis(project_id: str = PROJECT_ID) -> list[str]:
    """
    Returns a sorted list of enabled API names for the given project.

    Each name is the clean API identifier, e.g. 'bigquery.googleapis.com'.
    Prints findings with a sprawl warning when the count exceeds 40.
    """
    credentials, _ = google.auth.default()
    service = build("serviceusage", "v1", credentials=credentials)
    parent = f"projects/{project_id}"

    print("=" * 80)
    print(f"ENABLED APIs — {parent}")
    print("=" * 80)

    enabled = []
    try:
        request = service.services().list(
            parent=parent, filter="state:ENABLED", pageSize=200
        )
        while request is not None:
            response = request.execute()
            enabled.extend(response.get("services", []))
            request = service.services().list_next(request, response)
    except HttpError as e:
        if e.resp.status == 403:
            print("Permission denied: needs serviceusage.services.list, e.g.")
            print("roles/serviceusage.serviceUsageViewer (or roles/viewer) on the project.")
            return []
        raise

    # 'name' is projects/NNN/services/<api>; config.name is the clean API id.
    names = sorted(s["config"]["name"] for s in enabled)
    for n in names:
        print(f"  - {n}")

    print("-" * 80)
    print(f"Total enabled APIs: {len(names)}")

    # Heuristic: a single-purpose project typically needs 10-25 APIs. Counts
    # above ~40 usually include leftovers — diff against actual traffic in
    # the API dashboard / audit logs before disabling anything.
    if len(names) > 40:
        print("NOTE: >40 enabled APIs — review for sprawl; consider disabling unused")
        print("services and enforcing gcp.restrictServiceUsage going forward.")

    return names
