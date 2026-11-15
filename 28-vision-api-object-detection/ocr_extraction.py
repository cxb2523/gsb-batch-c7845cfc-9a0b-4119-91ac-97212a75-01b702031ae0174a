# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Vision API OCR — text_detection (sparse/photo text) and document_text_detection
(dense/printed documents with page structure).

OCR engine guide:
  - Vision API text_detection  : cheap (~$1.50/1K images), fast, plain text
                                   from photos/signs/screenshots. No structure.
  - Vision API doc_text_detection: page/layout-aware, handles multi-page structure,
                                   paragraph and block hierarchy.
  - Document AI               : page/layout-aware, multi-page PDFs, tables,
                                   form fields — for document pipelines.
  - Gemini multimodal          : OCR + understanding in one call; best when
                                   you need reasoning over the text, not just
                                   transcription.

PREREQUISITE: Cloud Vision API must be enabled.
  gcloud services enable vision.googleapis.com --project=your-project-id
"""

import os

from google.api_core import exceptions as gax
from google.cloud import vision

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

SIGN_IMAGE_URI = "gs://cloud-samples-data/vision/ocr/sign.jpg"
DOC_IMAGE_URI = "gs://cloud-samples-data/vision/document/custom_0773375000.jpg"


def get_vision_client() -> vision.ImageAnnotatorClient:
    """Create a Cloud Vision API client."""
    return vision.ImageAnnotatorClient()


def text_detection(client: vision.ImageAnnotatorClient, image_uri: str) -> str:
    """Detect sparse/natural-photo text in a GCS image.

    text_detection is optimized for signs, labels, and incidental text in
    photos. Annotation [0] is the full detected text block.

    Cost: ~$1.50 per 1,000 images (first 1,000 units/month free).

    Args:
        client: Vision API client.
        image_uri: GCS URI of the image.

    Returns:
        Detected text as a string, or empty string if none found.

    Raises:
        RuntimeError: If the Vision API returns an error.
    """
    response = client.text_detection(
        image=vision.Image(source=vision.ImageSource(image_uri=image_uri))
    )
    if response.error.message:
        raise RuntimeError(
            f"Vision API error: {response.error.message}\n"
            f"Ensure the API is enabled: "
            f"gcloud services enable vision.googleapis.com --project={PROJECT_ID}"
        )
    if response.text_annotations:
        return response.text_annotations[0].description.strip()
    return ""


def document_text_detection(client: vision.ImageAnnotatorClient, image_uri: str) -> dict:
    """Detect dense/printed document text with structure in a GCS image.

    Returns a full hierarchy: pages > blocks > paragraphs > words.
    Better suited for printed documents, screenshots, and structured text
    than text_detection.

    Args:
        client: Vision API client.
        image_uri: GCS URI of the image.

    Returns:
        Dict with keys:
          - full_text: complete text as a string
          - char_count: total character count
          - block_confidences: list of (confidence, paragraph_count) per block

    Raises:
        RuntimeError: If the Vision API returns an error.
    """
    response = client.document_text_detection(
        image=vision.Image(source=vision.ImageSource(image_uri=image_uri))
    )
    if response.error.message:
        raise RuntimeError(
            f"Vision API error: {response.error.message}\n"
            f"Ensure the API is enabled: "
            f"gcloud services enable vision.googleapis.com --project={PROJECT_ID}"
        )
    doc = response.full_text_annotation
    block_confidences = []
    for page in doc.pages:
        for block in page.blocks:
            block_confidences.append((block.confidence, len(block.paragraphs)))

    return {
        "full_text": doc.text,
        "char_count": len(doc.text),
        "block_confidences": block_confidences,
    }


def print_ocr_results(sign_text: str, doc_result: dict) -> None:
    """Print OCR results in a structured format."""
    print(f"=== text_detection: {SIGN_IMAGE_URI} ===")
    print(sign_text if sign_text else "(no text found)")

    print(f"\n=== document_text_detection: {DOC_IMAGE_URI} ===")
    print(f"Text length: {doc_result['char_count']:,} chars")
    print(f"--- first 500 chars ---\n{doc_result['full_text'][:500]}")
    print("\n--- block confidences (first 5) ---")
    for i, (conf, paras) in enumerate(doc_result["block_confidences"][:5]):
        print(f"  block {i + 1}: confidence={conf:.2f}, {paras} paragraphs")
