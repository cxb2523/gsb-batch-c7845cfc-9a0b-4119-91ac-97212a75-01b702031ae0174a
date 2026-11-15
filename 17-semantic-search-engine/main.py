# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 17 — Semantic Search Engine
Builds an in-memory semantic search index from a product catalog CSV,
then runs natural-language queries ranked by cosine similarity.

Flow:
  1. Load product_catalog.csv  →  deduplicate products
  2. Embed all products (RETRIEVAL_DOCUMENT task type)  →  numpy index
  3. Embed each query (RETRIEVAL_QUERY task type)
  4. Rank by cosine similarity  →  print top-K results with metadata

Set GOOGLE_CLOUD_PROJECT before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
"""

import os

from embedder import get_client
from search import CosineSimilarityIndex, ProductCatalogSearch, print_results

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

# -----------------------------------------------------------------------
# Demo corpus for pure cosine-similarity demo (Part 1)
# -----------------------------------------------------------------------
GCP_SERVICES = [
    "Cloud Run deploys stateless containers that scale automatically with traffic.",
    "BigQuery is a serverless data warehouse with SQL analytics over petabytes.",
    "Cloud Storage offers object storage with lifecycle rules and storage classes.",
    "Vertex AI provides managed training, tuning, and serving for ML models.",
    "Cloud SQL runs managed PostgreSQL, MySQL, and SQL Server instances.",
    "Pub/Sub decouples producers and consumers with global message delivery.",
    "Cloud Functions execute event-driven code without managing servers.",
    "GKE runs Kubernetes clusters with managed control planes and node pools.",
    "Cloud Armor protects applications from DDoS and web attacks at the edge.",
    "Dataflow runs Apache Beam pipelines for batch and streaming processing.",
    "Dataproc Serverless runs PySpark jobs without cluster provisioning.",
    "Cloud Composer orchestrates complex workflows using managed Apache Airflow.",
]

COSINE_QUERIES = [
    "Which service should I use for streaming data pipelines?",
    "I need a managed relational database for my web app.",
    "What is the best way to protect my API from attacks?",
]

# -----------------------------------------------------------------------
# Demo queries for catalog search (Part 2)
# -----------------------------------------------------------------------
CATALOG_QUERIES = [
    "cost control for predictable BigQuery workloads",
    "run a batch machine learning training job on GPU",
    "stream real-time events from Pub/Sub into my data warehouse",
    "store sensitive API credentials securely",
    "build a serverless API backend that scales to zero",
]


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_cosine_similarity(client) -> None:
    """
    Part 1: pure in-memory cosine similarity over a small GCP service corpus.
    Shows the core mechanic behind every RAG retriever.
    """
    index = CosineSimilarityIndex()
    index.build(client, GCP_SERVICES)

    for query in COSINE_QUERIES:
        results = index.search(client, query, top_k=3)
        print_results(results, query)


def demo_catalog_search(client) -> None:
    """
    Part 2: semantic search over a structured product catalog CSV.
    Matches natural-language intent to products even without keyword overlap.
    """
    catalog = ProductCatalogSearch()
    catalog.build(client)

    for query in CATALOG_QUERIES:
        results = catalog.search(client, query, top_k=3)
        print_results(results, query)

    print()
    print("Note: semantic search matches intent to products even when no")
    print("keyword overlaps — keyword search would miss 'cost control' → BigQuery.")


def main():
    print(f"Project 17 — Semantic Search Engine")
    print(f"Project : {PROJECT_ID}")

    client = get_client()

    section("PART 1 — Cosine Similarity Search (pure numpy, no vector DB)")
    demo_cosine_similarity(client)

    section("PART 2 — Product Catalog Semantic Search")
    demo_catalog_search(client)

    print()
    print("=" * 70)
    print("Done.")


if __name__ == "__main__":
    main()
