# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 14: Gemini Enterprise Features
Runs three enterprise-grade patterns in sequence:
  1. Google Search grounding — live web retrieval with source citations
  2. Context caching — cache a large PDF once, query it multiple times cheaply
  3. Batch prediction — async offline inference at 50% cost discount

Note on batch prediction: set GCS_BUCKET to a writable bucket in your project.
"""

import os

from grounding import run_grounding
from caching import run_caching
from batch_predict import run_batch_predict


def separator(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def main():
    separator("1. Google Search Grounding")
    run_grounding()

    separator("2. Context Caching")
    run_caching()

    separator("3. Batch Prediction")
    bucket = os.environ.get("GCS_BUCKET", "")
    if not bucket or bucket == "your-gcs-bucket":
        print("Skipping batch prediction: set GCS_BUCKET env var to a writable bucket.")
        print("  export GCS_BUCKET=your-actual-bucket-name")
    else:
        run_batch_predict()

    print("\nAll enterprise feature patterns complete.")


if __name__ == "__main__":
    main()
