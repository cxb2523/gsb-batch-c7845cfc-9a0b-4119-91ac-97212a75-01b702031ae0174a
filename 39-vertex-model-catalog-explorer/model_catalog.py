# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Model catalog module: lists all publisher/foundation models available to the
project via the unified Google Gen AI SDK and returns structured catalog data.
Useful for model discovery, capability filtering, and version audits.
"""

import os

from google import genai

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")


def format_actions(model) -> str:
    """Extract supported / tunable hints from a model object, best-effort."""
    actions = []
    for attr in ("supported_actions", "supported_generation_methods"):
        value = getattr(model, attr, None)
        if value:
            actions.extend(str(v) for v in value)
    tuned = getattr(model, "tuned_model_info", None)
    if tuned:
        actions.append("tuned")
    return ", ".join(actions) if actions else "-"


def list_publisher_models(project_id: str = PROJECT_ID, location: str = LOCATION) -> list[dict]:
    """
    Lists all publisher models available to the project via the Gen AI SDK.

    Returns a list of dicts, each containing:
      - 'name': str full model resource name
      - 'display_name': str human-readable name
      - 'actions': str comma-separated supported actions
    """
    client = genai.Client(vertexai=True, project=project_id, location=location)

    models = []
    for model in client.models.list():
        models.append({
            "name": model.name or "-",
            "display_name": model.display_name or "-",
            "actions": format_actions(model),
        })
    return models


def filter_models_by_capability(models: list[dict], keyword: str) -> list[dict]:
    """
    Returns models whose name, display_name, or actions contain the keyword (case-insensitive).
    Useful for filtering by capability (e.g., 'gemini', 'embed', 'imagen', 'code').
    """
    kw = keyword.lower()
    return [
        m for m in models
        if kw in m["name"].lower()
        or kw in m["display_name"].lower()
        or kw in m["actions"].lower()
    ]
