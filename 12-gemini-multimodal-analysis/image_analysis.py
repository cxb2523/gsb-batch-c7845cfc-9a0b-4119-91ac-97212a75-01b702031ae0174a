# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Image understanding with Gemini — two ingestion patterns:
  1. GCS URI (Part.from_uri): Vertex AI reads the object directly from GCS.
     Zero-copy, preferred for pipeline data already on GCS.
  2. Local bytes (Part.from_bytes): inline raw bytes, used for user uploads
     or files not on GCS (keeps pipeline stateless for small payloads).
"""

import os

import requests
from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"

# Public sample image — Google Cloud sample assets (scones on a table).
GCS_IMAGE_URI = "gs://cloud-samples-data/generative-ai/image/scones.jpg"
HTTP_IMAGE_URL = (
    "https://storage.googleapis.com/cloud-samples-data/generative-ai/image/scones.jpg"
)

IMAGE_PROMPT = (
    "1) Describe this image in one sentence. "
    "2) List the main objects you can see, comma-separated."
)


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def analyze_image_from_gcs(client, gcs_uri=GCS_IMAGE_URI, prompt=IMAGE_PROMPT):
    """
    Sends a GCS-hosted image to Gemini via Part.from_uri.

    Vertex AI reads the GCS object server-side — bytes never pass through
    your local machine. Most efficient for images already stored in GCS.

    Args:
        client: genai.Client instance.
        gcs_uri: GCS URI of the image (gs://bucket/object).
        prompt: Instruction for the model.

    Returns:
        str: Model response text.
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_uri(file_uri=gcs_uri, mime_type="image/jpeg"),
            prompt,
        ],
        config=types.GenerateContentConfig(max_output_tokens=512),
    )
    return response.text


def analyze_image_from_bytes(client, http_url=HTTP_IMAGE_URL, prompt=IMAGE_PROMPT):
    """
    Downloads an image and sends it to Gemini via Part.from_bytes.

    Use this pattern for user-uploaded images or files not on GCS.
    Note: inline payloads are subject to request size limits — keep small.

    Args:
        client: genai.Client instance.
        http_url: URL to download the image from.
        prompt: Instruction for the model.

    Returns:
        dict with keys: byte_count, text
    """
    image_bytes = requests.get(http_url, timeout=30).content
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            prompt,
        ],
        config=types.GenerateContentConfig(max_output_tokens=512),
    )
    return {
        "byte_count": len(image_bytes),
        "text": response.text,
    }


def run_image_analysis():
    """Entry point for the image analysis demo."""
    client = create_client()

    print("=== Image via GCS URI (Part.from_uri) ===")
    result_gcs = analyze_image_from_gcs(client)
    print(result_gcs)

    print("\n=== Image via local bytes (Part.from_bytes) ===")
    result_bytes = analyze_image_from_bytes(client)
    print(f"(downloaded {result_bytes['byte_count']} bytes to simulate a local file)")
    print(result_bytes["text"])
