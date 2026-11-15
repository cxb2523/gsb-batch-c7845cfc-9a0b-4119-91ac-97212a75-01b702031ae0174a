# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Classic Vision API label and landmark detection.
Still the right tool for high-volume, fixed-taxonomy tagging at predictable
per-image pricing when you don't need reasoning or follow-up questions.

PREREQUISITE: Cloud Vision API must be enabled.
  gcloud services enable vision.googleapis.com --project=your-project-id
"""

import os

from google.api_core import exceptions as gax
from google.cloud import vision

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

LABEL_IMAGE_URI = "gs://cloud-samples-data/vision/label/setagaya.jpeg"
LANDMARK_IMAGE_URI = "gs://cloud-samples-data/vision/landmark/eiffel_tower.jpg"


def get_vision_client() -> vision.ImageAnnotatorClient:
    """Create a Cloud Vision API client."""
    return vision.ImageAnnotatorClient()


def detect_labels(client: vision.ImageAnnotatorClient, image_uri: str, top_n: int = 10) -> list:
    """Run label detection on a GCS image URI.

    Cost: ~$1.50 per 1,000 images per feature (first 1,000 units/month free).
    When to prefer Vision API labels over Gemini: fixed label taxonomy,
    very high volume at predictable pricing, sub-second latency without
    prompt engineering.

    Args:
        client: Vision API client.
        image_uri: GCS URI of the image.
        top_n: Maximum number of label annotations to return.

    Returns:
        List of label annotation objects (with .description and .score).

    Raises:
        RuntimeError: If the Vision API returns an error (e.g., API not enabled).
    """
    response = client.label_detection(
        image=vision.Image(source=vision.ImageSource(image_uri=image_uri))
    )
    if response.error.message:
        raise RuntimeError(
            f"Vision API error: {response.error.message}\n"
            f"Ensure the API is enabled: "
            f"gcloud services enable vision.googleapis.com --project={PROJECT_ID}"
        )
    return list(response.label_annotations[:top_n])


def detect_landmarks(client: vision.ImageAnnotatorClient, image_uri: str) -> list:
    """Run landmark detection on a GCS image URI.

    Returns the landmark name plus lat/long — useful for geo-tagging user photos.

    Args:
        client: Vision API client.
        image_uri: GCS URI of the image.

    Returns:
        List of landmark annotation objects (with .description and .locations).

    Raises:
        RuntimeError: If the Vision API returns an error.
    """
    response = client.landmark_detection(
        image=vision.Image(source=vision.ImageSource(image_uri=image_uri))
    )
    if response.error.message:
        raise RuntimeError(
            f"Vision API error: {response.error.message}\n"
            f"Ensure the API is enabled: "
            f"gcloud services enable vision.googleapis.com --project={PROJECT_ID}"
        )
    return list(response.landmark_annotations)


def print_labels(labels: list, image_uri: str) -> None:
    """Print label detection results."""
    print(f"=== LABEL DETECTION: {image_uri} ===")
    for label in labels:
        print(f"  [{label.score:.2f}] {label.description}")


def print_landmarks(landmarks: list, image_uri: str) -> None:
    """Print landmark detection results including lat/long."""
    print(f"=== LANDMARK DETECTION: {image_uri} ===")
    for lm in landmarks:
        print(f"  [{lm.score:.2f}] {lm.description}")
        for loc in lm.locations:
            print(f"      lat/lng: {loc.lat_lng.latitude:.4f}, {loc.lat_lng.longitude:.4f}")
