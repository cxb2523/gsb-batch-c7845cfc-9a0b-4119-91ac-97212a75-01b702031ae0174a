# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Object detection with bounding boxes using Gemini 2.5 Flash.
Returns normalized bounding boxes on a 0-1000 scale with structured JSON output.
"""

import json

from google import genai
from google.genai import types

MODEL = "gemini-2.5-flash"

DETECTION_PROMPT = (
    "Detect all prominent objects in this image. Return ONLY a JSON array, "
    'each item: {"label": str, "box_2d": [ymin, xmin, ymax, xmax]} where '
    "coordinates are integers normalized to a 0-1000 scale."
)


def get_client(project_id: str, location: str = "us-central1") -> genai.Client:
    """Create a Vertex AI Gen AI client."""
    return genai.Client(vertexai=True, project=project_id, location=location)


def detect_objects(client: genai.Client, image_gcs_uri: str) -> list[dict]:
    """Detect objects and return normalized bounding boxes as a list of dicts.

    Gemini's bounding box convention: [ymin, xmin, ymax, xmax] on a
    normalized 0-1000 grid, independent of actual image resolution.

    A schema constraint is used to force the decoder to emit valid,
    structurally correct JSON — without it, long object lists can produce
    subtly malformed output.

    Cost: single gemini-2.5-flash call, well under $0.01.

    Args:
        client: Gen AI client.
        image_gcs_uri: GCS URI of the image (gs://bucket/path.jpg).

    Returns:
        List of dicts: [{"label": str, "box_2d": [ymin, xmin, ymax, xmax]}, ...]
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=[
            types.Part.from_uri(file_uri=image_gcs_uri, mime_type="image/jpeg"),
            DETECTION_PROMPT,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "label": types.Schema(type=types.Type.STRING),
                        "box_2d": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.INTEGER),
                        ),
                    },
                    required=["label", "box_2d"],
                ),
            ),
            temperature=0.0,  # deterministic boxes
        ),
    )
    return json.loads(response.text)


def print_detections(detections: list[dict], img_w: int = 1000, img_h: int = 667) -> None:
    """Print detections in both normalized (0-1000) and pixel coordinates.

    Args:
        detections: List returned by detect_objects().
        img_w: Actual image width in pixels (default 1000 for the sample image).
        img_h: Actual image height in pixels (default 667 for the sample image).
    """
    print(f"=== {len(detections)} OBJECTS DETECTED (0-1000 normalized) ===")
    for det in detections:
        ymin, xmin, ymax, xmax = det["box_2d"]
        print(f"  {det['label']:<25s} box=[y:{ymin}-{ymax}, x:{xmin}-{xmax}]")

    # Pixel mapping:
    #   pixel_x = x_norm / 1000 * image_width
    #   pixel_y = y_norm / 1000 * image_height
    print(f"\n=== SAME BOXES IN PIXELS (assuming {img_w}x{img_h}) ===")
    for det in detections:
        ymin, xmin, ymax, xmax = det["box_2d"]
        px = (
            int(xmin / 1000 * img_w), int(ymin / 1000 * img_h),
            int(xmax / 1000 * img_w), int(ymax / 1000 * img_h),
        )
        print(f"  {det['label']:<25s} (left,top,right,bottom)={px}")
