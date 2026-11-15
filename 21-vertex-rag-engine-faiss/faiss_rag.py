# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
FAISS local RAG — embed chunks with Gemini Embeddings API, build an
IndexFlatIP index in process memory, search by inner product (== cosine
after L2 normalisation), then generate a grounded answer.

When to use local FAISS: single-process apps, batch jobs, corpora that fit
in RAM (~millions of vectors on a modern machine), no concurrent writers.
Upgrade to Vertex AI Vector Search, AlloyDB pgvector, or BigQuery
VECTOR_SEARCH when you need multi-replica serving, filtered queries at
scale, incremental updates from many writers, or operational SLAs.
"""

import os

import faiss
import numpy as np
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
EMBEDDING_MODEL = "gemini-embedding-001"
GENERATION_MODEL = "gemini-2.5-flash"
TOP_K = 3


def build_index(chunks: list[str]) -> tuple:
    """
    Embed all chunks and build a FAISS IndexFlatIP (exact brute-force inner product).
    Returns (index, client) so the caller can reuse the client for query embedding.
    """
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    doc_resp = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=chunks,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    vectors = np.array([e.values for e in doc_resp.embeddings], dtype=np.float32)
    faiss.normalize_L2(vectors)   # normalize so inner product equals cosine similarity

    dim = vectors.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)
    print(f"[FAISS] Index built: IndexFlatIP, dim={dim}, ntotal={index.ntotal}")
    return index, client


def search(index: object, client: object, chunks: list[str], question: str, top_k: int = TOP_K) -> list[str]:
    """Embed the question and return the top-k matching chunks."""
    q_resp = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[question],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    q_vec = np.array([q_resp.embeddings[0].values], dtype=np.float32)
    faiss.normalize_L2(q_vec)
    scores, ids = index.search(q_vec, top_k)

    retrieved = [chunks[i] for i in ids[0]]
    print(f"[FAISS] Question: {question!r}")
    print(f"[FAISS] Top {top_k} hits:")
    for score, chunk in zip(scores[0], retrieved):
        print(f"  [{score:.4f}] {chunk}")
    return retrieved


def generate_answer(client: object, retrieved: list[str], question: str) -> str:
    """Generate a grounded answer from the retrieved chunks."""
    prompt = (
        "Answer using ONLY this context:\n\n"
        + "\n".join(retrieved)
        + f"\n\nQuestion: {question}"
    )
    answer = client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
    print(f"[FAISS] Answer:\n{answer.text}")
    return answer.text


def run(chunks: list[str], question: str) -> str:
    """
    End-to-end FAISS RAG: build index → search → generate.
    Returns the generated answer text.
    """
    index, client = build_index(chunks)
    retrieved = search(index, client, chunks, question)
    return generate_answer(client, retrieved, question)
