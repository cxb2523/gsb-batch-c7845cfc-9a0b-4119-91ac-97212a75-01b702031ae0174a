# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 19 — RAG Quickstart: In-Memory Pipeline
Loads a BigQuery knowledge base article, runs three chunking strategies,
builds an in-memory embedding index, retrieves relevant chunks, and
generates grounded answers using Gemini 2.5 Flash.

Flow:
  1. Load data/knowledge_base.txt  (500-word BigQuery article)
  2. Compare fixed-size / overlapping / separator chunking strategies
  3. Build in-memory index with the recommended strategy
  4. Run 3 sample questions through the full RAG pipeline
  5. Print retrieved chunks + grounded Gemini answers

Set GOOGLE_CLOUD_PROJECT before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
"""

import os
from pathlib import Path

from chunker import (
    fixed_size,
    overlapping,
    by_separator,
    by_separator_with_max,
    print_chunk_report,
)
from rag import get_client, run_rag, print_rag_result

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
KNOWLEDGE_BASE = Path(__file__).parent / "data" / "knowledge_base.txt"

QUESTIONS = [
    "What are the two pricing models in BigQuery and when should I use each?",
    "How do partitioned and clustered tables reduce query costs?",
    "How does BigQuery integrate with Vertex AI and machine learning workflows?",
]

TOP_K = 3


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_chunking_strategies(corpus: str) -> list[str]:
    """
    Compare all chunking strategies and return the recommended chunks
    (separator-with-max) for use in the RAG pipeline.
    """
    print(f"Corpus length: {len(corpus)} chars")
    print()

    print_chunk_report("1. Fixed-size (200 chars)", fixed_size(corpus, 200))
    print_chunk_report("2. Overlapping (500 chars, 100 overlap)", overlapping(corpus, 500, 100))
    print_chunk_report("3. Separator / paragraph (\\n\\n)", by_separator(corpus))
    print_chunk_report("4. Separator + max-size fallback (recommended)", by_separator_with_max(corpus))

    print()
    print("Rules of thumb:")
    print("  - Start with separator chunking + a max-size fallback (strategy 4).")
    print("  - Add overlap only when answers straddle paragraph boundaries.")
    print("  - Smaller chunks = precise retrieval, less context per hit.")
    print("  - Bigger chunks = more context, noisier similarity scores.")

    # Return the recommended strategy for the actual RAG demo
    return by_separator_with_max(corpus)


def main():
    print("Project 19 — RAG Quickstart: In-Memory Pipeline")
    print(f"Project : {PROJECT_ID}")

    corpus = KNOWLEDGE_BASE.read_text()
    print(f"Knowledge base: {KNOWLEDGE_BASE.name}")

    # -----------------------------------------------------------------------
    section("PART 1 — Chunking Strategy Comparison")
    # -----------------------------------------------------------------------
    chunks = demo_chunking_strategies(corpus)
    print(f"\nUsing {len(chunks)} chunks (separator + max-size) for RAG pipeline.")

    # -----------------------------------------------------------------------
    section("PART 2 — In-Memory RAG Pipeline")
    # -----------------------------------------------------------------------
    client = get_client()

    print(f"Building embedding index ({len(chunks)} chunks)...")
    print()

    for i, question in enumerate(QUESTIONS, start=1):
        print(f"\n--- Question {i} of {len(QUESTIONS)} ---")
        result = run_rag(client, chunks, question, top_k=TOP_K)
        print_rag_result(result)
        print()

    print("=" * 70)
    print("Done.")
    print()
    print("Next steps:")
    print("  - Project 20: add citations to RAG answers (PDF source)")
    print("  - Projects 21-22: Vertex AI RAG Engine + FAISS + hybrid reranking")


if __name__ == "__main__":
    main()
