# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 22 — Hybrid RAG with BM25 + Dense Vector Search + Cross-Encoder Reranking

Pipeline:
  1. BM25 keyword ranking  (exact token match — wins on rare tokens/codes/SKUs)
  2. Dense vector ranking  (semantic similarity — wins on paraphrase/synonyms)
  3. Reciprocal Rank Fusion (rank-based fusion — no score calibration needed)
  4. Gemini cross-encoder reranking (joint query+doc attention over fused top-k)
"""

import os

import keyword_search
import vector_search
import reranker
from google import genai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GENERATION_MODEL = "gemini-2.5-flash"
FUSED_TOP_K = 4   # candidates passed to the Gemini reranker


def load_documents(path: str = "data/documents.txt") -> list[str]:
    """Split the corpus file into section-level documents on double newlines."""
    with open(path) as f:
        text = f.read()
    docs = [p.strip() for p in text.split("\n\n") if p.strip()]
    print(f"Loaded {len(docs)} documents from {path}")
    return docs


def run_hybrid_search(docs: list[str], query: str, fused_top_k: int = FUSED_TOP_K):
    """
    Full hybrid search + rerank pipeline:
      BM25 rank -> vector rank -> RRF fusion -> Gemini rerank
    Returns the reranked list of (doc_id, score) tuples.
    """
    print(f"\nQuery: {query!r}")
    print("-" * 70)

    # Stage 1a: BM25 keyword ranking
    bm25_index = keyword_search.build_bm25_index(docs)
    bm25_ranking = keyword_search.bm25_rank(bm25_index, query)
    print(f"[BM25]   Top 3: {[docs[i][:60].strip() + '...' for i in bm25_ranking[:3]]}")

    # Stage 1b: Dense vector ranking
    matrix, client = vector_search.build_vector_index(docs)
    vec_ranking = vector_search.vector_rank(matrix, client, query)
    print(f"[Vector] Top 3: {[docs[i][:60].strip() + '...' for i in vec_ranking[:3]]}")

    # Stage 2: Reciprocal Rank Fusion
    fused = reranker.reciprocal_rank_fusion([bm25_ranking, vec_ranking])[:fused_top_k]
    print(f"[RRF]    Fused top {fused_top_k}: {[docs[i][:60].strip() + '...' for i in fused]}")

    # Stage 3: Gemini cross-encoder reranker over the fused top-k
    print("-" * 70)
    reranked = reranker.gemini_rerank(client, query, docs, fused)

    return reranked, client


def generate_grounded_answer(client, docs: list[str], reranked: list[tuple], query: str) -> str:
    """Generate a final answer grounded in the top reranked documents."""
    top_docs = [docs[doc_id] for doc_id, _ in reranked[:3]]
    context = "\n\n".join(top_docs)
    prompt = f"Answer using ONLY this context:\n\n{context}\n\nQuestion: {query}"
    resp = client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
    return resp.text


def main():
    docs = load_documents("data/documents.txt")

    queries = [
        # Exact-token query — BM25 will outperform vector on "INFORMATION_SCHEMA"
        "How do I track BigQuery costs using INFORMATION_SCHEMA?",
        # Semantic query — vector search will outperform BM25 on paraphrase
        "What options exist for deploying containers on GCP without managing servers?",
        # Mixed query — both retrievers contribute
        "How does message ordering work in Pub/Sub streaming systems?",
    ]

    for query in queries:
        print("\n" + "=" * 70)
        reranked, client = run_hybrid_search(docs, query)
        answer = generate_grounded_answer(client, docs, reranked, query)
        print("-" * 70)
        print(f"Grounded answer:\n{answer}")

    print("\n" + "=" * 70)
    print("Why hybrid + rerank:")
    print("  BM25 wins on rare exact tokens (error codes, schema names, SKUs).")
    print("  Vectors win on paraphrase and semantic synonyms.")
    print("  RRF fuses both lists without needing score calibration.")
    print("  Gemini cross-encoder reranks the small candidate set with full")
    print("  joint attention — the most accurate stage, applied cheaply.")
    print("=" * 70)


if __name__ == "__main__":
    main()
