# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 20 — PDF RAG with Citations
Loads a document (text file or real PDF), chunks it with overlap,
builds an in-memory embedding index, retrieves relevant passages,
and generates Gemini answers with mandatory inline [source-N] citations.

Each citation is mapped back to the original chunk for audit.
Hallucinated citation ids (model invents an id it wasn't given) are flagged.

Flow:
  1. Load data/sample_document.txt  (or a real PDF via --pdf flag)
  2. Chunk with 1200-char overlapping windows (overlap=150)
  3. Embed corpus with RETRIEVAL_DOCUMENT task type
  4. For each question: retrieve → generate with citations → validate citations
  5. Print full citation audit trail

Usage:
  # Default: uses local text file (no PDF download needed)
  python main.py

  # Real PDF mode (downloads Gemini 1.5 paper from public GCS):
  python main.py --pdf

Set GOOGLE_CLOUD_PROJECT before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
"""

import os
import sys
from pathlib import Path

from pdf_loader import (
    load_and_chunk,
    print_load_summary,
    DEMO_PDF_URL,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
)
from citation_rag import get_client, build_index, run_citation_rag, print_cited_answer

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
SAMPLE_DOC = Path(__file__).parent / "data" / "sample_document.txt"

# Questions for the local text file (GCP data engineering best practices)
LOCAL_QUESTIONS = [
    "What are the best practices for ingesting streaming data into BigQuery?",
    "How do partitioned tables and clustering reduce BigQuery query costs?",
    "What security controls should I apply to protect sensitive data in BigQuery?",
]

# Questions for the real PDF (Gemini 1.5 paper)
PDF_QUESTIONS = [
    "What context window size does the paper highlight, and why does it matter?",
    "How does the model handle long-context tasks compared to previous models?",
    "What evaluation benchmarks does the paper use to assess performance?",
]

TOP_K = 4


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main():
    use_pdf = "--pdf" in sys.argv

    print("Project 20 — PDF RAG with Citations")
    print(f"Project : {PROJECT_ID}")
    print(f"Mode    : {'PDF (Gemini 1.5 paper from GCS)' if use_pdf else 'Local text file'}")

    # -----------------------------------------------------------------------
    section("PART 1 — Document Loading and Chunking")
    # -----------------------------------------------------------------------

    if use_pdf:
        chunks, meta = load_and_chunk(
            pdf_url=DEMO_PDF_URL,
            chunk_size=DEFAULT_CHUNK_SIZE,
            overlap=DEFAULT_OVERLAP,
        )
        questions = PDF_QUESTIONS
    else:
        chunks, meta = load_and_chunk(
            text_path=SAMPLE_DOC,
            chunk_size=DEFAULT_CHUNK_SIZE,
            overlap=DEFAULT_OVERLAP,
        )
        questions = LOCAL_QUESTIONS

    print_load_summary(meta)

    # -----------------------------------------------------------------------
    section("PART 2 — Building Embedding Index")
    # -----------------------------------------------------------------------
    client = get_client()
    print(f"Embedding {len(chunks)} chunks with gemini-embedding-001...")
    index = build_index(client, chunks)
    print(f"Index ready. Shape: {index.shape}")

    # -----------------------------------------------------------------------
    section("PART 3 — RAG with Citations")
    # -----------------------------------------------------------------------
    print(f"Running {len(questions)} questions through citation RAG pipeline...")

    for i, question in enumerate(questions, start=1):
        print(f"\n--- Question {i} of {len(questions)} ---")
        result = run_citation_rag(client, chunks, question, top_k=TOP_K)
        print_cited_answer(result)
        print()

    print("=" * 70)
    print("Done.")
    print()
    print("Key patterns demonstrated:")
    print("  - Stable source ids: original chunk index, not rank position")
    print("  - Forced citations: [source-N] in every factual claim")
    print("  - Hallucination detection: ids not in retrieved set are flagged")
    print("  - Audit trail: cited vs not-cited vs hallucinated per answer")


if __name__ == "__main__":
    main()
