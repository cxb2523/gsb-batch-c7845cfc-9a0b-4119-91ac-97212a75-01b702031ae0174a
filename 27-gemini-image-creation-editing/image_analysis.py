# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Gemini multimodal image analysis — description, structured object extraction,
and OCR using gemini-2.5-flash. One model replaces several classic Vision API
features and adds reasoning/follow-up capability on top.
"""

import os

from google import genai
from google.genai import types

MODEL = "gemini-2.5-flash"


def get_analysis_client(project_id: str, location: str = "us-central1") -> genai.Client:
    """Create a Vertex AI Gen AI client for analysis tasks."""
    return genai.Client(vertexai=True, project=project_id, location=location)


def _ask(client: genai.Client, image_uri: str, prompt: str, response_mime_type: str = None) -> str:
    """Send a multimodal prompt referencing a GCS image URI.

    Gemini on Vertex AI reads gs:// URIs directly — no download needed.
    Cost: gemini-2.5-flash is fractions of a cent per image+prompt call
    ($0.30/M input tokens; an image is typically a few hundred tokens).

    Args:
        client: Gen AI client.
        image_uri: GCS URI of the image (gs://bucket/path.jpg).
        prompt: Text instruction for the model.
        response_mime_type: If "application/json", forces valid JSON output.

    Returns:
        Model response text.
    """
    config = None
    if response_mime_type:
        config = types.GenerateContentConfig(response_mime_type=response_mime_type)

    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_uri(file_uri=image_uri, mime_type="image/jpeg"),
            prompt,
        ],
        config=config,
    )
    return response.text


def describe_image(client: genai.Client, image_uri: str) -> str:
    """Natural-language description of the image.

    Replaces Vision API label detection with richer, contextual output.
    """
    return _ask(client, image_uri, "Describe this image in 2-3 sentences.")


def extract_objects_json(client: genai.Client, image_uri: str) -> str:
    """Structured object extraction as JSON.

    response_mime_type="application/json" forces valid JSON output —
    no markdown fences to strip. Returns a JSON array of objects with
    label, count, and color fields.
    """
    return _ask(
        client,
        image_uri,
        'List every distinct object in the image as JSON: '
        '[{"object": str, "count": int, "color": str}]',
        response_mime_type="application/json",
    )


def extract_text_ocr(client: genai.Client, image_uri: str) -> str:
    """OCR — transcribe any visible text from the image.

    Replaces Vision API text_detection for casual/photo text. For
    high-volume scanned documents use Document AI instead.
    Returns the transcribed text or "NO TEXT FOUND".
    """
    return _ask(
        client,
        image_uri,
        "Transcribe any text visible in this image. "
        "If there is no text, reply exactly: NO TEXT FOUND.",
    )


def analyze_local_image(client: genai.Client, local_path: str) -> dict:
    """Analyze a local image file by uploading bytes inline.

    Useful when the image is newly generated (not yet in GCS).

    Args:
        client: Gen AI client.
        local_path: Path to a local PNG/JPEG file.

    Returns:
        Dict with keys: description, objects_json, ocr_text.
    """
    with open(local_path, "rb") as f:
        image_bytes = f.read()

    ext = os.path.splitext(local_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"

    def ask_local(prompt, response_mime_type=None):
        config = None
        if response_mime_type:
            config = types.GenerateContentConfig(response_mime_type=response_mime_type)
        response = client.models.generate_content(
            model=MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime),
                prompt,
            ],
            config=config,
        )
        return response.text

    return {
        "description": ask_local("Describe this image in 2-3 sentences."),
        "objects_json": ask_local(
            'List every distinct object in the image as JSON: '
            '[{"object": str, "count": int, "color": str}]',
            response_mime_type="application/json",
        ),
        "ocr_text": ask_local(
            "Transcribe any text visible in this image. "
            "If there is no text, reply exactly: NO TEXT FOUND."
        ),
    }
