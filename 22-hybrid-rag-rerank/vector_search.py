# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Dense vector search component using Gemini Embeddings API.

Dense retrieval embeds both documents and the query into a shared semantic
space and ranks by cosine similarity. It excels at paraphrase and synonym
matching — a query for "refund policy" matches documents that say "money back
guarantee" even without any shared tokens. The weakness is exact rare-token
queries where BM25 dominates, which is why hybrid search combines both.
"""

import os

import numpy as np
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
EMBEDDING_MODEL = "gemini-embedding-001"


def build_vector_index(docs: list[str]) -> tuple:
    """
    Embed all documents with Gemini Embeddings API and return (matrix, client).
    matrix shape: (n_docs, embedding_dim), L2-normalized for cosine similarity.
    """
    client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    resp = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=docs,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    matrix = np.array([e.values for e in resp.embeddings], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)   # L2-normalize
    print(f"[Vector] Index built: {matrix.shape[0]} docs, dim={matrix.shape[1]}")
    return matrix, client


def vector_rank(matrix: np.ndarray, client: object, query: str, top_k: int | None = None) -> list[int]:
    """
    Embed the query and rank all document indices by cosine similarity.
    Returns a list of doc indices sorted by descending similarity (best first).
    If top_k is provided, returns only the top-k indices.
    """
    q_resp = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[query],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    q_vec = np.array(q_resp.embeddings[0].values, dtype=np.float32)
    q_vec /= np.linalg.norm(q_vec)
    similarities = matrix @ q_vec
    ranked = list(np.argsort(similarities)[::-1])
    return ranked[:top_k] if top_k else ranked
