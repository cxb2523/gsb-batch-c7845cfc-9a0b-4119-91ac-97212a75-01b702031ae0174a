# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Dataflow Template Launch — demo entry point.

Launches the Word_Count Dataflow managed template job via the REST API.

DEFAULT: DRY_RUN=true — prints job parameters without submitting.
To submit a REAL paid Dataflow job:
    export DRY_RUN=false
    python main.py

COST WARNING: A real Dataflow job spins up worker VMs (n1-standard-1 by
default) billed per second. Word_Count on the sample dataset costs a few
cents, but always monitor the Dataflow console to confirm the job finishes.
"""

import os

from dataflow_launcher import launch_template, DRY_RUN, PROJECT_ID, REGION, TEMPLATE_PATH


def main():
    print("=" * 65)
    print("  GCP DATA ENGINEERING HUB — Project 32")
    print("  Dataflow Template Launch")
    print("=" * 65)
    print(f"\n  Project  : {PROJECT_ID}")
    print(f"  Region   : {REGION}")
    print(f"  DRY_RUN  : {DRY_RUN}")
    if DRY_RUN:
        print("\n  [INFO] Running in DRY RUN mode. No job will be submitted.")
        print("  [INFO] Set DRY_RUN=false to submit a real paid Dataflow job.\n")
    else:
        print("\n  [WARNING] DRY_RUN is FALSE — a real paid job WILL be submitted.\n")

    print("-" * 65)
    result = launch_template(
        project_id=PROJECT_ID,
        region=REGION,
        template_path=TEMPLATE_PATH,
        dry_run=DRY_RUN,
    )

    print("\n" + "=" * 65)
    if result.get("dry_run"):
        print("  Result: DRY RUN complete — no job submitted.")
    else:
        print(f"  Result: Job submitted successfully.")
        print(f"  Job ID: {result.get('job_id')}")
    print("=" * 65)


if __name__ == "__main__":
    main()
