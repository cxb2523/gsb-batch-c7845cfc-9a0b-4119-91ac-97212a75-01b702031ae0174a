# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 21 — Vertex RAG Engine + FAISS + BigQuery VECTOR_SEARCH
Demonstrates three RAG retrieval backends in sequence:
  1. Vertex AI RAG Engine (fully managed, billable corpus)
  2. FAISS local index (in-process, no infra)
  3. BigQuery VECTOR_SEARCH (SQL-native vector retrieval)
"""

import sys

import faiss_rag
import vertex_rag
import bq_vector_search

# ---------------------------------------------------------------------------
# Shared corpus for FAISS and BQ demos (loaded from data/documents.txt)
# ---------------------------------------------------------------------------
def load_chunks(path: str = "data/documents.txt") -> list[str]:
    """Split the document corpus into paragraph-level chunks."""
    with open(path) as f:
        text = f.read()
    # Split on blank lines and strip whitespace; drop empties
    chunks = [p.strip() for p in text.split("\n\n") if p.strip()]
    print(f"Loaded {len(chunks)} chunks from {path}")
    return chunks


# ---------------------------------------------------------------------------
# Demo 1 — Vertex AI RAG Engine
# ---------------------------------------------------------------------------
def demo_vertex_rag():
    print("\n" + "=" * 70)
    print("DEMO 1: Vertex AI RAG Engine (managed corpus)")
    print("=" * 70)
    print("NOTE: This creates a billable managed corpus and deletes it on exit.")
    print("      Set RAG_ENGINE_LOCATION env var to change region (default: europe-west4)")
    question = "What are the key capabilities described in this document?"
    answer = vertex_rag.run(question)
    return answer


# ---------------------------------------------------------------------------
# Demo 2 — FAISS local index
# ---------------------------------------------------------------------------
def demo_faiss(chunks: list[str]):
    print("\n" + "=" * 70)
    print("DEMO 2: FAISS local index (in-process, exact brute-force)")
    print("=" * 70)
    question = "How does BigQuery handle petabyte-scale analytics?"
    answer = faiss_rag.run(chunks, question)
    return answer


# ---------------------------------------------------------------------------
# Demo 3 — BigQuery VECTOR_SEARCH
# ---------------------------------------------------------------------------
def demo_bq_vector_search(chunks: list[str]):
    print("\n" + "=" * 70)
    print("DEMO 3: BigQuery VECTOR_SEARCH (SQL-native vector retrieval)")
    print("=" * 70)
    question = "What GCP services support real-time data streaming?"
    answer = bq_vector_search.run(chunks, question)
    return answer


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    chunks = load_chunks("data/documents.txt")

    # Choose which demos to run via CLI args; default = all three.
    # Usage: python main.py faiss bq      (skip Vertex RAG to avoid corpus billing)
    #        python main.py vertex        (only managed RAG)
    #        python main.py              (all three)
    targets = set(sys.argv[1:]) if len(sys.argv) > 1 else {"vertex", "faiss", "bq"}

    if "vertex" in targets:
        demo_vertex_rag()

    if "faiss" in targets:
        demo_faiss(chunks)

    if "bq" in targets:
        demo_bq_vector_search(chunks)

    print("\n" + "=" * 70)
    print("All demos complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
