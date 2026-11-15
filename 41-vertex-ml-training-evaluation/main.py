# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Vertex AI ML Training & Evaluation Pipeline.

Orchestrates three stages:
  1. Batch prediction via BigQuery (Gemini at 50% discount)
  2. Supervised fine-tuning data prep (DRY_RUN=True by default — no billable job)
  3. GenAI evaluation using Gemini-as-judge rubric scoring

Run: python main.py
     DRY_RUN=false python main.py   (to submit a real fine-tuning job)
"""

import os

from batch_predict import run_batch_prediction
from fine_tuning import run_fine_tuning
from evaluation import run_evaluation

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"


def main():
    print("=" * 78)
    print("Vertex AI ML Training & Evaluation Pipeline")
    print(f"Project: {PROJECT_ID} | Location: {LOCATION} | DRY_RUN: {DRY_RUN}")
    print("=" * 78)

    # Stage 1: Batch prediction
    print("\n### STAGE 1: Gemini Batch Prediction (BigQuery) ###\n")
    batch_job = run_batch_prediction()

    # Stage 2: Supervised fine-tuning (DRY_RUN=True by default)
    print("\n### STAGE 2: Supervised Fine-Tuning (DRY_RUN={}) ###\n".format(DRY_RUN))
    tuning_job = run_fine_tuning()

    # Stage 3: GenAI evaluation
    print("\n### STAGE 3: GenAI Evaluation (Gemini-as-Judge) ###\n")
    eval_results = run_evaluation()

    print("\n" + "=" * 78)
    print("Pipeline complete.")
    if batch_job:
        print(f"  Batch job state : {batch_job.state}")
    if tuning_job:
        print(f"  Tuning job name : {tuning_job.name}")
    else:
        print("  Tuning job      : skipped (DRY_RUN=true)")
    if eval_results:
        verdict = eval_results.get("pairwise_verdict", {})
        print(f"  Eval winner     : {verdict.get('winner', 'N/A')} — {verdict.get('reason', '')}")
    print("=" * 78)


if __name__ == "__main__":
    main()
