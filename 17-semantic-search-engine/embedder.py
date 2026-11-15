# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Embedding generation functions for the semantic search engine.
Wraps the Vertex AI genai client to produce normalized numpy vectors
suitable for cosine similarity search.
"""

import os

import numpy as np
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
EMBEDDING_MODEL = "gemini-embedding-001"


def get_client() -> genai.Client:
    """Return a configured Vertex AI genai client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def embed_documents(client: genai.Client, texts: list[str]) -> np.ndarray:
    """
    Embed a list of document strings in a single batched API call.

    Uses RETRIEVAL_DOCUMENT task type, which tunes the embedding space
    for long, content-shaped passages and maximizes retrieval recall when
    paired with RETRIEVAL_QUERY on the query side.

    Returns a float32 matrix of shape (n_docs, embedding_dim), L2-normalized
    so that dot product equals cosine similarity.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    matrix = np.array([e.values for e in response.embeddings], dtype=np.float32)
    # Normalize rows so dot product == cosine similarity downstream.
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / norms


def embed_query(client: genai.Client, query: str) -> np.ndarray:
    """
    Embed a single query string using RETRIEVAL_QUERY task type.

    Queries are short and intent-shaped; documents are long and content-shaped.
    Using asymmetric task types projects both into a shared space optimized
    for that asymmetry, so a 5-word query lands close to the passage that
    answers it even without keyword overlap.

    Returns a normalized 1-D float32 array.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[query],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    vec = np.array(response.embeddings[0].values, dtype=np.float32)
    return vec / np.linalg.norm(vec)
