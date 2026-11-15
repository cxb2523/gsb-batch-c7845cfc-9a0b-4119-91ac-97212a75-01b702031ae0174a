# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Vertex AI Endpoint FinOps Audit — Find Undeploy Candidates.

Lists all Vertex AI online prediction endpoints, their deployed models and
machine specs, and flags endpoints with zero-traffic-split models as undeploy
candidates. READ-ONLY: prints the undeploy/delete commands, never runs them.

Deployed endpoints bill per node-hour 24/7 — this is the #1 source of silent
Vertex AI spend.
"""

import os

from google.cloud import aiplatform

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")


def describe_machine(deployed_model):
    """Return a human-readable machine/replica string for a deployed model."""
    dr = getattr(deployed_model, "dedicated_resources", None)
    if dr and getattr(dr, "machine_spec", None) and dr.machine_spec.machine_type:
        spec = dr.machine_spec
        machine = spec.machine_type
        if getattr(spec, "accelerator_type", None) and str(spec.accelerator_type) != "0":
            machine += f" + {spec.accelerator_count}x {spec.accelerator_type}"
        return f"{machine} (min={dr.min_replica_count}, max={dr.max_replica_count})"
    ar = getattr(deployed_model, "automatic_resources", None)
    if ar:
        return f"automatic (min={ar.min_replica_count}, max={ar.max_replica_count})"
    return "unknown"


def audit_endpoints():
    """
    Audit all Vertex AI endpoints for billing waste.

    Returns a list of (endpoint, deployed_model_or_None, reason) tuples
    identifying undeploy candidates.
    """
    print(f"[endpoint_finops] Project: {PROJECT_ID} | Location: {LOCATION}")
    print("[endpoint_finops] READ-ONLY audit — no resources will be modified.")
    print("=" * 78)

    aiplatform.init(project=PROJECT_ID, location=LOCATION)
    endpoints = aiplatform.Endpoint.list()

    if not endpoints:
        print("[endpoint_finops] No endpoints found. Nothing is billing for online prediction.")
        return []

    candidates = []
    for ep in endpoints:
        gca = ep.gca_resource
        deployed = list(gca.deployed_models)
        traffic = dict(gca.traffic_split)

        print(f"\nEndpoint   : {ep.display_name}")
        print(f"Resource   : {ep.resource_name}")
        print(f"Models     : {len(deployed)} deployed")

        if not deployed:
            print("  (empty endpoint — costs nothing but is clutter)")
            candidates.append((ep, None, "no deployed models"))
            continue

        for dm in deployed:
            pct = traffic.get(dm.id, 0)
            machine = describe_machine(dm)
            print(f"  - id={dm.id} model={dm.model.split('/')[-1]}")
            print(f"    machine={machine} traffic={pct}%")
            # 0% traffic split = paying node-hours for a model serving nothing.
            if pct == 0:
                candidates.append((ep, dm, "0% traffic split"))

    return candidates


def print_undeploy_candidates(candidates):
    """Print actionable undeploy/delete commands for flagged endpoints."""
    print()
    print("=" * 78)
    print(f"UNDEPLOY CANDIDATES: {len(candidates)}")
    print("=" * 78)

    if not candidates:
        print("[endpoint_finops] None flagged. Cross-check request counts in Cloud Monitoring")
        print("(metric: aiplatform.googleapis.com/prediction/online/prediction_count)")
        print("before assuming active endpoints are still needed.")
        return

    for ep, dm, reason in candidates:
        print(f"\n* {ep.display_name} — {reason}")
        if dm is not None:
            print("  Undeploy (stops node-hour billing for this model):")
            print(f"    gcloud ai endpoints undeploy-model {ep.name} \\")
            print(f"      --deployed-model-id={dm.id} \\")
            print(f"      --region={LOCATION} --project={PROJECT_ID}")
        print("  Delete endpoint (only when no models remain):")
        print(f"    gcloud ai endpoints delete {ep.name} \\")
        print(f"      --region={LOCATION} --project={PROJECT_ID}")

    print()
    print("Tip: confirm zero traffic over 14+ days in Cloud Monitoring before")
    print("undeploying anything on production paths.")


def run_endpoint_finops():
    """Full endpoint FinOps audit flow."""
    candidates = audit_endpoints()
    print_undeploy_candidates(candidates)
    return candidates
