# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 16 — Text Embeddings Fundamentals
Runs three embedding patterns in sequence:
  1. Single embedding inspection (vector length, norm, first values)
  2. Batch embedding with task types + cosine similarity ranking
  3. Output dimensionality trade-offs (3072 vs 768 vs 256 dims)

Set GOOGLE_CLOUD_PROJECT before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
"""

import os
from pathlib import Path

from embeddings import get_client as get_embedding_client
from embeddings import demo_single_embedding, demo_batch_embeddings
from embeddings import embed_batch_documents, embed_query, rank_documents, SAMPLE_DOCUMENTS
from dimensionality import get_client as get_dim_client
from dimensionality import demo_dimensionality_tradeoffs

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATA_FILE = Path(__file__).parent / "data" / "sample_texts.txt"


def load_sample_texts() -> list[str]:
    """Load the 20 sample product/tech descriptions from data/sample_texts.txt."""
    texts = [line.strip() for line in DATA_FILE.read_text().splitlines() if line.strip()]
    return texts


def demo_corpus_from_file(client) -> None:
    """Embed all 20 sample texts from file and show a query result."""
    texts = load_sample_texts()
    print(f"Loaded {len(texts)} texts from {DATA_FILE.name}")
    print("-" * 70)

    query = "serverless compute platform for running containers"
    doc_matrix = embed_batch_documents(client, texts)
    query_vec = embed_query(client, query)
    ranked = rank_documents(doc_matrix, query_vec, texts, top_k=3)

    print(f"Query: {query!r}")
    print()
    for rank, (score, doc) in enumerate(ranked, start=1):
        print(f"  {rank}. [{score:.4f}] {doc}")


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main():
    print(f"Project 16 — Text Embeddings Fundamentals")
    print(f"Project : {PROJECT_ID}")

    client = get_embedding_client()

    # -----------------------------------------------------------------------
    section("PART 1 — Single Embedding Inspection")
    # -----------------------------------------------------------------------
    demo_single_embedding(client)

    # -----------------------------------------------------------------------
    section("PART 2 — Batch Embeddings with Task Types")
    # -----------------------------------------------------------------------
    demo_batch_embeddings(client)

    # -----------------------------------------------------------------------
    section("PART 3 — Corpus from File (20 Sample Texts)")
    # -----------------------------------------------------------------------
    demo_corpus_from_file(client)

    # -----------------------------------------------------------------------
    section("PART 4 — Output Dimensionality Trade-offs")
    # -----------------------------------------------------------------------
    dim_client = get_dim_client()
    demo_dimensionality_tradeoffs(dim_client)

    print()
    print("=" * 70)
    print("Done.")


if __name__ == "__main__":
    main()
