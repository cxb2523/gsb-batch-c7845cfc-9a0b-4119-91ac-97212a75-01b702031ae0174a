# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Output dimensionality control for gemini-embedding-001.
Source: 03_output_dimensionality.py

gemini-embedding-001 is trained with Matryoshka Representation Learning (MRL).
The first N dims of the 3072-dim vector are themselves a usable embedding.
Truncating to 768 or 256 cuts storage/compute ~4x or ~12x with minimal recall loss.

Storage math per 1M vectors (float32):
  3072 dims -> ~12.3 GB | 768 dims -> ~3.1 GB | 256 dims -> ~1.0 GB
"""

import os

import numpy as np
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
EMBEDDING_MODEL = "gemini-embedding-001"

TEXT_A = "Vertex AI hosts foundation models behind a managed API."
TEXT_B = "Managed APIs on Vertex AI serve Google's foundation models."

DIMS_TO_COMPARE = (3072, 768, 256)


def get_client():
    """Return a configured Vertex AI genai client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def embed_pair_at_dim(client, text_a: str, text_b: str, dim: int) -> tuple[np.ndarray, np.ndarray]:
    """
    Embed two texts at a specific output dimensionality.

    CRITICAL: Only the full 3072-dim output is unit-length. Truncated outputs
    must be re-normalized before cosine / dot-product search, otherwise
    similarity scores are skewed by vector magnitude.

    Returns two float32 numpy arrays of shape (dim,).
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[text_a, text_b],
        config=types.EmbedContentConfig(
            task_type="SEMANTIC_SIMILARITY",
            output_dimensionality=dim,
        ),
    )
    return (
        np.array(response.embeddings[0].values, dtype=np.float32),
        np.array(response.embeddings[1].values, dtype=np.float32),
    )


def cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors after L2 normalization."""
    a = vec_a / np.linalg.norm(vec_a)
    b = vec_b / np.linalg.norm(vec_b)
    return float(a @ b)


def storage_bytes(dim: int, dtype_bytes: int = 4) -> int:
    """Return bytes per vector for a given dimensionality (default float32 = 4 bytes)."""
    return dim * dtype_bytes


def demo_dimensionality_tradeoffs(client) -> None:
    """
    Compare cosine similarity and storage cost across 3072, 768, and 256 dims.
    Demonstrates that cosine barely changes while storage drops ~12x.
    """
    print(f"Text A : {TEXT_A!r}")
    print(f"Text B : {TEXT_B!r}")
    print("-" * 70)

    header = f"{'dims':>6} | {'raw norm':>9} | {'cosine (normalized)':>20} | {'bytes/vec':>10} | {'GB per 1M vecs':>14}"
    print(header)
    print("-" * len(header))

    for dim in DIMS_TO_COMPARE:
        vec_a, vec_b = embed_pair_at_dim(client, TEXT_A, TEXT_B, dim)
        raw_norm = float(np.linalg.norm(vec_a))
        cosine = cosine_similarity(vec_a, vec_b)
        bpv = storage_bytes(dim)
        gb_per_million = (bpv * 1_000_000) / (1024 ** 3)
        print(f"{dim:>6} | {raw_norm:>9.4f} | {cosine:>20.4f} | {bpv:>10,} | {gb_per_million:>14.2f}")

    print("-" * 70)
    print("Key insight: cosine similarity barely moves while storage drops ~12x.")
    print()
    print("Rule of thumb:")
    print("  3072 dims — when maximum recall matters (small corpora, high stakes)")
    print("   768 dims — most production retrieval use cases (sweet spot)")
    print("   256 dims — huge corpora with a re-ranker in front")
