# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Minimal Vertex AI Pipeline (KFP v2) — Prep -> Train Stub.

Defines a two-step KFP v2 pipeline (data prep -> train stub) from lightweight
Python components, compiles it to JSON, and optionally submits it as a Vertex
AI PipelineJob. The skeleton for any ML workflow you want scheduled, audited,
and reproducible.

COST GATED: defaults to DRY_RUN=True (compile only). Each real pipeline step
runs on billable ephemeral compute plus a small per-run pipeline fee.
"""

import os

from kfp import compiler, dsl

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

GCS_BUCKET = os.environ.get("GOOGLE_CLOUD_BUCKET", "your-gcs-bucket")
PIPELINE_ROOT = f"gs://{GCS_BUCKET}/pipeline-root"
SPEC_PATH = "minimal_pipeline_spec.json"


@dsl.component(base_image="python:3.12-slim")
def prep_data(num_rows: int) -> str:
    """Pretend to prepare a dataset; returns a fake dataset handle."""
    handle = f"dataset://demo/{num_rows}-rows"
    print(f"[prep] prepared {num_rows} rows -> {handle}")
    return handle


@dsl.component(base_image="python:3.12-slim")
def train_stub(dataset_handle: str, learning_rate: float) -> str:
    """Pretend to train; returns a fake model URI."""
    print(f"[train] training on {dataset_handle} with lr={learning_rate}")
    fake_accuracy = 0.91
    print(f"[train] done, accuracy={fake_accuracy}")
    return "model://demo/v1"


@dsl.pipeline(
    name="gcp-hub-minimal-pipeline",
    description="GCP Data Engineering Hub minimal prep->train demo pipeline",
)
def minimal_pipeline(num_rows: int = 100, learning_rate: float = 0.01):
    prep_task = prep_data(num_rows=num_rows)
    train_stub(dataset_handle=prep_task.output, learning_rate=learning_rate)


def compile_pipeline():
    """Compile the KFP pipeline to a JSON spec file."""
    print(f"[pipelines] Compiling pipeline -> {SPEC_PATH}")
    compiler.Compiler().compile(
        pipeline_func=minimal_pipeline, package_path=SPEC_PATH
    )
    print(f"[pipelines] Compiled spec written: {os.path.abspath(SPEC_PATH)}")
    return SPEC_PATH


def run_pipeline():
    """Compile the pipeline and optionally submit it to Vertex AI."""
    print(f"[pipelines] Project: {PROJECT_ID} | Location: {LOCATION} | DRY_RUN: {DRY_RUN}")
    print("-" * 78)

    spec_path = compile_pipeline()

    if DRY_RUN:
        print()
        print("[pipelines] DRY RUN — compile only, nothing submitted. A real run would:")
        print()
        print("   from google.cloud import aiplatform")
        print("   aiplatform.init(project=PROJECT_ID, location=LOCATION)")
        print("   job = aiplatform.PipelineJob(")
        print("       display_name='gcp-hub-minimal-pipeline',")
        print(f"       template_path='{spec_path}',")
        print(f"       pipeline_root='{PIPELINE_ROOT}',")
        print("       parameter_values={'num_rows': 100, 'learning_rate': 0.01})")
        print("   job.submit()")
        print()
        print("It would execute: prep_data -> train_stub, each step in its own")
        print("container on ephemeral Vertex compute (billed per step runtime,")
        print("plus ~$0.03 per pipeline run). Artifacts land under:")
        print(f"   {PIPELINE_ROOT}")
        print()
        print("Enable real submission: DRY_RUN=false python main.py")
        return None

    from google.cloud import aiplatform

    aiplatform.init(project=PROJECT_ID, location=LOCATION)

    print("[pipelines] Submitting PipelineJob (billable compute per step)...")
    job = aiplatform.PipelineJob(
        display_name="gcp-hub-minimal-pipeline",
        template_path=spec_path,
        pipeline_root=PIPELINE_ROOT,
        parameter_values={"num_rows": 100, "learning_rate": 0.01},
        enable_caching=True,
    )
    job.submit()

    print(f"[pipelines] Job resource : {job.resource_name}")
    print(f"[pipelines] Console URL  : {job._dashboard_uri()}")
    print()
    print("Monitor: job.wait()  (blocks until completion)")
    print("Cleanup: delete old runs and GCS artifacts under the pipeline root")
    print("to keep storage tidy.")

    return job
