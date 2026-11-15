# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Multimodal embeddings: image + text in a shared 1408-dim vector space.
Source: 07_multimodal_embeddings.py

Use cases: image search by text query, cross-modal similarity, recommendation seeds.

Model: multimodalembedding@001
  - Regional model — us-central1 only (NOT global like gemini-embedding-001)
  - Served via the Vertex AI SDK (vision_models), NOT the google-genai embed_content API
  - Vectors from different models live in incompatible spaces — never mix models
"""

import os

import numpy as np
import vertexai
from vertexai.vision_models import Image, MultiModalEmbeddingModel

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = "us-central1"  # multimodalembedding@001 is regional, not global
MODEL_ID = "multimodalembedding@001"

# Public GCS image — no auth required to embed via the model
IMAGE_URI = "gs://cloud-samples-data/generative-ai/image/scones.jpg"

MATCHING_TEXT = "blueberry scones and a cup of coffee on a table"
UNRELATED_TEXT = "quarterly financial report for a logistics company"


def cosine_similarity(a: list | np.ndarray, b: list | np.ndarray) -> float:
    """Return cosine similarity between two vectors."""
    a, b = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def get_model() -> MultiModalEmbeddingModel:
    """Initialize Vertex AI and return the multimodal embedding model."""
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    return MultiModalEmbeddingModel.from_pretrained(MODEL_ID)


def embed_image(model: MultiModalEmbeddingModel, gcs_uri: str) -> list[float]:
    """
    Embed a GCS image into the 1408-dim shared multimodal space.
    Returns the image embedding as a list of floats.
    """
    image = Image(gcs_uri=gcs_uri)
    return model.get_embeddings(image=image).image_embedding


def embed_text_multimodal(model: MultiModalEmbeddingModel, text: str) -> list[float]:
    """
    Embed a text string using multimodalembedding@001 (NOT gemini-embedding-001).
    Must use the same model for both image and text — cross-model comparison is invalid.
    Returns the text embedding as a list of floats.
    """
    return model.get_embeddings(contextual_text=text).text_embedding


def run_multimodal_demo(model: MultiModalEmbeddingModel) -> None:
    """
    Embed one image and two text strings, then compare cross-modal cosine similarity.

    Expected: matching text (scones) scores higher than unrelated text (financial report).
    Note: cross-modal scores are lower in absolute terms (~0.1-0.3) than text-to-text
    scores. Always compare relative scores, not against a text-only threshold.
    """
    print(f"Model  : {MODEL_ID} (region: {LOCATION})")
    print(f"Image  : {IMAGE_URI}")
    print()

    print("Embedding image...")
    image_vec = embed_image(model, IMAGE_URI)
    print(f"Image vector dims : {len(image_vec)}")
    print()

    text_pairs = [
        ("matching", MATCHING_TEXT),
        ("unrelated", UNRELATED_TEXT),
    ]

    print("Embedding texts with same model (shared vector space)...")
    print("-" * 70)
    results = []
    for label, text in text_pairs:
        text_vec = embed_text_multimodal(model, text)
        sim = cosine_similarity(image_vec, text_vec)
        results.append((label, text, sim))
        print(f"Image vs {label} text")
        print(f"  text   : {text!r}")
        print(f"  cosine : {sim:.4f}")
        print()

    winner = max(results, key=lambda x: x[2])
    verdict = "matching text scores higher (correct)" if winner[0] == "matching" else "unexpected ranking"
    print("-" * 70)
    print(f"Result : {verdict}")
    print("Note   : cross-modal similarity (~0.1-0.3) is lower than text-to-text (~0.7-0.9)")
    print("         Compare relative scores, not absolute values borrowed from text-only models.")
