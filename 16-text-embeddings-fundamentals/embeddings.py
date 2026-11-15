# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Single and batch embedding functions using gemini-embedding-001 on Vertex AI.
Covers: single-text embedding with vector inspection, batch embedding with
task types, and cosine similarity ranking.
"""

import os

import numpy as np
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
EMBEDDING_MODEL = "gemini-embedding-001"


def get_client():
    """Return a configured Vertex AI genai client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


# ---------------------------------------------------------------------------
# Pattern 1: Single embedding (source: 01_first_embedding.py)
# ---------------------------------------------------------------------------

def embed_single(client, text: str, task_type: str = "SEMANTIC_SIMILARITY") -> list[float]:
    """
    Embed a single text string and return the raw float vector.

    An embedding is a fixed-length float vector where geometric closeness
    (cosine / dot product) encodes semantic similarity of the input text.
    gemini-embedding-001 returns 3072-dim unit-length vectors by default.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[text],
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return response.embeddings[0].values


def inspect_vector(vector: list[float], label: str = "Vector") -> None:
    """Print key statistics for an embedding vector."""
    norm = sum(v * v for v in vector) ** 0.5
    print(f"{label}")
    print(f"  length  : {len(vector)}")
    print(f"  first 8 : {[round(v, 6) for v in vector[:8]]}")
    print(f"  min/max : {min(vector):.6f} / {max(vector):.6f}")
    print(f"  L2 norm : {norm:.6f}  (should be ~1.0 at full 3072 dims)")


def demo_single_embedding(client) -> None:
    """Demonstrate embedding a single sentence and inspecting the output."""
    text = "Cloud Run scales containers to zero when there is no traffic."
    print(f"Model  : {EMBEDDING_MODEL}")
    print(f"Input  : {text!r}")
    print("-" * 70)

    vector = embed_single(client, text)
    inspect_vector(vector, label="Embedding")


# ---------------------------------------------------------------------------
# Pattern 2: Batch embedding with task types (source: 02_batch_embeddings_task_types.py)
# ---------------------------------------------------------------------------

SAMPLE_DOCUMENTS = [
    "BigQuery slot reservations let you trade on-demand pricing for capacity-based pricing.",
    "Cloud Run jobs are designed for run-to-completion workloads rather than serving traffic.",
    "Vertex AI Pipelines orchestrate ML workflows using a serverless execution engine.",
    "GKE Autopilot manages nodes for you and bills per-pod resource requests.",
    "Pub/Sub provides at-least-once delivery with optional exactly-once semantics.",
    "Cloud Storage lifecycle rules automatically delete or transition objects by age or condition.",
    "Dataflow autoscales workers during a streaming job to handle spiky ingestion rates.",
    "Cloud Spanner replicates data synchronously across regions for five-nines availability.",
]

SAMPLE_QUERY = "How do I cut BigQuery costs for predictable workloads?"


def embed_batch_documents(client, documents: list[str]) -> np.ndarray:
    """
    Embed a list of documents in a single API call using RETRIEVAL_DOCUMENT.
    Returns a float32 matrix of shape (n_docs, dims).

    Task type RETRIEVAL_DOCUMENT tunes the embedding space for long,
    content-shaped passages. Pairing with RETRIEVAL_QUERY measurably
    improves retrieval recall over using SEMANTIC_SIMILARITY for both.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=documents,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return np.array([e.values for e in response.embeddings], dtype=np.float32)


def embed_query(client, query: str) -> np.ndarray:
    """
    Embed a single query string using RETRIEVAL_QUERY task type.
    Returns a 1-D float32 array.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=[query],
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return np.array(response.embeddings[0].values, dtype=np.float32)


def rank_documents(doc_matrix: np.ndarray, query_vec: np.ndarray,
                   documents: list[str], top_k: int = 3) -> list[tuple[float, str]]:
    """
    Rank documents by cosine similarity to the query vector.
    Normalizes both matrices before dot-product to get cosine scores.
    Returns list of (score, document) tuples sorted descending.
    """
    doc_norms = doc_matrix / np.linalg.norm(doc_matrix, axis=1, keepdims=True)
    q_norm = query_vec / np.linalg.norm(query_vec)
    scores = doc_norms @ q_norm
    top_idx = np.argsort(scores)[::-1][:top_k]
    return [(float(scores[i]), documents[i]) for i in top_idx]


def demo_batch_embeddings(client) -> None:
    """Demonstrate batch embedding + query ranking with correct task types."""
    doc_matrix = embed_batch_documents(client, SAMPLE_DOCUMENTS)
    query_vec = embed_query(client, SAMPLE_QUERY)

    print(f"Embedded {len(SAMPLE_DOCUMENTS)} documents  shape={doc_matrix.shape}")
    print(f"Query   : {SAMPLE_QUERY!r}")
    print("-" * 70)

    ranked = rank_documents(doc_matrix, query_vec, SAMPLE_DOCUMENTS, top_k=3)
    for rank, (score, doc) in enumerate(ranked, start=1):
        print(f"  {rank}. score={score:.4f}  {doc[:70]}")

    print("\nTop hit should be the BigQuery reservations sentence.")
