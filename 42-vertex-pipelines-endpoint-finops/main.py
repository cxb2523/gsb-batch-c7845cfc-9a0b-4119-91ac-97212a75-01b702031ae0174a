# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Vertex AI Pipelines & Endpoint FinOps.

Orchestrates two stages:
  1. Endpoint FinOps audit — lists all Vertex AI endpoints, flags 0%-traffic
     deployed models as undeploy candidates (READ-ONLY, prints gcloud commands)
  2. Minimal Vertex AI Pipeline — compiles a KFP v2 prep->train pipeline and
     optionally submits it (DRY_RUN=True by default)

Run: python main.py
     DRY_RUN=false python main.py   (to submit a real pipeline job)
"""

import os

from endpoint_finops import run_endpoint_finops
from pipelines import run_pipeline

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"


def main():
    print("=" * 78)
    print("Vertex AI Pipelines & Endpoint FinOps")
    print(f"Project: {PROJECT_ID} | Location: {LOCATION} | DRY_RUN: {DRY_RUN}")
    print("=" * 78)

    # Stage 1: Endpoint FinOps audit (always READ-ONLY)
    print("\n### STAGE 1: Vertex AI Endpoint FinOps Audit ###\n")
    candidates = run_endpoint_finops()

    # Stage 2: Minimal Vertex Pipeline (DRY_RUN=True by default)
    print("\n### STAGE 2: Minimal Vertex Pipeline (DRY_RUN={}) ###\n".format(DRY_RUN))
    pipeline_job = run_pipeline()

    print("\n" + "=" * 78)
    print("Run complete.")
    print(f"  Endpoint undeploy candidates : {len(candidates)}")
    if pipeline_job:
        print(f"  Pipeline job submitted       : {pipeline_job.resource_name}")
    else:
        print("  Pipeline job                 : compiled only (DRY_RUN=true)")
    print("=" * 78)


if __name__ == "__main__":
    main()
