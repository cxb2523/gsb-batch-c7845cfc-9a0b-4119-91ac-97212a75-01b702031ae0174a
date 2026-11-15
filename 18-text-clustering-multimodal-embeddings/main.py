# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 18 — Text Clustering, Multimodal Embeddings & BigQuery Vectors
Demonstrates three advanced embedding patterns in sequence:
  1. Text clustering with KMeans + Gemini cluster naming
  2. Multimodal embeddings: image + text in a shared vector space
  3. BigQuery ML.GENERATE_EMBEDDING (requires BQ connection setup)

Set GOOGLE_CLOUD_PROJECT before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id

Part 3 (BigQuery) requires a Cloud Resource connection — run Part 3 separately
after completing the one-time setup described in bq_vectors.py.
Pass --skip-bq to run only Parts 1 and 2:
  python main.py --skip-bq
"""

import os
import sys

from clustering import get_client, load_texts, run_clustering
from multimodal import get_model, run_multimodal_demo
from bq_vectors import run_bq_vectors_demo

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
N_CLUSTERS = 4


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main():
    skip_bq = "--skip-bq" in sys.argv

    print("Project 18 — Text Clustering, Multimodal Embeddings & BigQuery Vectors")
    print(f"Project : {PROJECT_ID}")

    # -----------------------------------------------------------------------
    section("PART 1 — Text Clustering (KMeans + Gemini cluster naming)")
    # -----------------------------------------------------------------------
    client = get_client()
    texts = load_texts()
    print(f"Loaded {len(texts)} sample texts from data/sample_texts.txt")
    run_clustering(client, texts, n_clusters=N_CLUSTERS)

    # -----------------------------------------------------------------------
    section("PART 2 — Multimodal Embeddings (image + text in shared space)")
    # -----------------------------------------------------------------------
    model = get_model()
    run_multimodal_demo(model)

    # -----------------------------------------------------------------------
    section("PART 3 — BigQuery ML.GENERATE_EMBEDDING")
    # -----------------------------------------------------------------------
    if skip_bq:
        print("Skipping BigQuery demo (--skip-bq flag set).")
        print("To run: remove --skip-bq and complete the one-time BQ connection setup.")
    else:
        print("Running BigQuery embedding demo (needs Cloud Resource connection)...")
        print("If not set up, setup instructions will be printed automatically.")
        run_bq_vectors_demo()

    print()
    print("=" * 70)
    print("Done.")


if __name__ == "__main__":
    main()
