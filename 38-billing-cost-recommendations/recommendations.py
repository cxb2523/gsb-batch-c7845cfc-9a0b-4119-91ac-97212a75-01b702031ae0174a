# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Recommender module: sweeps Google's own cost recommenders (idle VMs, VM
rightsizing, idle disks/images, commitment purchases) across common locations
and prints recommendations with estimated savings.
Free money — Google literally tells you what to shut down or resize.
"""

import os

from google.api_core import exceptions
from google.cloud import recommender_v1

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

# The cost-focused recommenders worth checking weekly. Each needs the matching
# recommender.*.list permission — roles/recommender.viewer covers compute ones;
# billing CUD recommendations may live on the billing account instead.
RECOMMENDERS = [
    "google.compute.instance.IdleResourceRecommender",        # idle VMs
    "google.compute.instance.MachineTypeRecommender",         # rightsizing
    "google.compute.disk.IdleResourceRecommender",            # idle disks
    "google.compute.image.IdleResourceRecommender",           # idle images
    "google.compute.commitment.UsageCommitmentRecommender",   # CUD purchases
]

# Recommendations are zonal/regional — you must query each location. Sweep the
# zones/regions you actually deploy to; 'global' catches a few recommender types.
DEFAULT_LOCATIONS = [
    "global",
    "us-central1", "us-central1-a", "us-central1-b", "us-central1-c",
    "asia-south1", "asia-south1-a", "asia-south1-b",
    "us-east1", "us-east1-b",
]


def fetch_cost_recommendations(
    project_id: str = PROJECT_ID,
    locations: list = None,
    recommenders: list = None,
) -> dict:
    """
    Sweeps cost recommenders across the given locations and returns a summary.

    Returns a dict with:
      - 'found': int total recommendations
      - 'total_savings': dict of currency -> float (negative = savings)
    """
    if locations is None:
        locations = DEFAULT_LOCATIONS
    if recommenders is None:
        recommenders = RECOMMENDERS

    client = recommender_v1.RecommenderClient()
    print("=" * 80)
    print(f"COST RECOMMENDATIONS — project {project_id}")
    print("=" * 80)

    found = 0
    total_savings = {}  # currency -> units
    permission_warned = False

    for rec_id in recommenders:
        short = rec_id.split(".", 2)[-1]
        print(f"\n[{short}]")
        any_for_rec = False
        for loc in locations:
            parent = (f"projects/{project_id}/locations/{loc}/recommenders/{rec_id}")
            try:
                recs = list(client.list_recommendations(parent=parent))
            except exceptions.PermissionDenied:
                if not permission_warned:
                    print("  Permission denied: needs roles/recommender.viewer (or the")
                    print("  per-recommender viewer roles) on the project; commitment")
                    print("  recommendations may require billing-account-level access.")
                    permission_warned = True
                break  # same denial will repeat for every location
            except (exceptions.InvalidArgument, exceptions.NotFound):
                continue  # recommender not available in this location — normal

            for rec in recs:
                found += 1
                any_for_rec = True
                print(f"  - [{loc}] {rec.description}")
                # primary_impact carries the money: cost_projection is the
                # estimated delta over 'duration' (usually 30 days). Negative
                # units = savings. This number is what goes in your FinOps report.
                impact = rec.primary_impact
                if impact.category == recommender_v1.Impact.Category.COST:
                    cost = impact.cost_projection.cost
                    amount = cost.units + cost.nanos / 1e9
                    days = impact.cost_projection.duration.seconds // 86400
                    print(f"      est. impact: {amount:.2f} {cost.currency_code} "
                          f"over {days}d (negative = savings)")
                    total_savings[cost.currency_code] = (
                        total_savings.get(cost.currency_code, 0) + amount
                    )
                print(f"      state: {rec.state_info.state.name}")
        if not any_for_rec and not permission_warned:
            print("  (none — nothing idle/oversized found in swept locations)")

    print("\n" + "=" * 80)
    print(f"Total recommendations: {found}")
    for currency, amount in total_savings.items():
        print(f"Projected cost impact: {amount:.2f} {currency} (negative = savings)")
    if found == 0:
        print("Clean sweep. Note: recommenders need ~8-14 days of usage telemetry,")
        print("so brand-new projects legitimately return nothing.")

    return {"found": found, "total_savings": total_savings}
