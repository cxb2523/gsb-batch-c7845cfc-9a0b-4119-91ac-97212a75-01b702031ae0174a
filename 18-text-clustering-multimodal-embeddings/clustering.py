# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Text clustering using KMeans on Vertex AI embeddings.
Each cluster is named by a Gemini generation call.
Source: 06_text_clustering.py

Use case: ticket triage, feedback analysis, topic discovery — no labeled data needed.
"""

import os
from pathlib import Path

import numpy as np
from google import genai
from google.genai import types
from sklearn.cluster import KMeans

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
EMBEDDING_MODEL = "gemini-embedding-001"
GENERATION_MODEL = "gemini-2.5-flash"

DATA_FILE = Path(__file__).parent / "data" / "sample_texts.txt"


def get_client() -> genai.Client:
    """Return a configured Vertex AI genai client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def load_texts(path: Path = DATA_FILE) -> list[str]:
    """Load non-empty lines from the sample texts file."""
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def embed_for_clustering(client: genai.Client, texts: list[str]) -> np.ndarray:
    """
    Embed texts using the CLUSTERING task type.

    CLUSTERING optimizes the embedding space for grouping similar documents
    together, rather than for retrieval (RETRIEVAL_DOCUMENT) or semantic
    comparison (SEMANTIC_SIMILARITY). Using the wrong task type degrades
    cluster cohesion without any obvious error.

    Returns a normalized float32 matrix of shape (n_texts, embedding_dim).
    KMeans on normalized vectors approximates spherical clustering in cosine space.
    """
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type="CLUSTERING"),
    )
    matrix = np.array([e.values for e in response.embeddings], dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix


def cluster_texts(matrix: np.ndarray, n_clusters: int, random_state: int = 42) -> np.ndarray:
    """
    Run KMeans on the embedding matrix.
    n_init=10 runs 10 random inits and picks the best to avoid local minima.
    Returns a label array of shape (n_texts,).
    """
    kmeans = KMeans(n_clusters=n_clusters, n_init=10, random_state=random_state)
    return kmeans.fit_predict(matrix)


def name_cluster(client: genai.Client, members: list[str]) -> str:
    """
    Ask Gemini to generate a 2-5 word topic label for a set of texts.
    Returns the stripped label string.
    """
    prompt = (
        "Give a 2-5 word topic label for this group of support messages. "
        "Reply with the label only, nothing else.\n\n- " + "\n- ".join(members)
    )
    response = client.models.generate_content(model=GENERATION_MODEL, contents=prompt)
    return response.text.strip()


def run_clustering(client: genai.Client, texts: list[str], n_clusters: int = 4) -> None:
    """
    Embed → cluster → name each cluster with Gemini → print results.
    """
    print(f"Embedding {len(texts)} texts (task_type=CLUSTERING)...")
    matrix = embed_for_clustering(client, texts)

    print(f"Clustering into {n_clusters} groups with KMeans...")
    labels = cluster_texts(matrix, n_clusters)

    print(f"\nClustered {len(texts)} texts into {n_clusters} clusters")
    print("=" * 70)

    for cluster_id in range(n_clusters):
        members = [t for t, lbl in zip(texts, labels) if lbl == cluster_id]
        label = name_cluster(client, members)

        print(f"\nCluster {cluster_id}: {label}  ({len(members)} items)")
        for text in members:
            print(f"   - {text}")
