# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Safety settings and blocked-response handling for production Gemini calls.

Configures per-category harm thresholds and shows how to handle blocked
responses gracefully — check prompt_feedback and finish_reason instead of
assuming response.text always exists (it raises an exception when blocked).

Threshold ladder (strictest → most permissive):
  BLOCK_LOW_AND_ABOVE > BLOCK_MEDIUM_AND_ABOVE > BLOCK_ONLY_HIGH > BLOCK_NONE

Tuning guidance:
- Kids / public consumer apps: BLOCK_LOW_AND_ABOVE across all categories.
- Internal / enterprise tools: BLOCK_MEDIUM_AND_ABOVE is usually appropriate.
- Security research / moderation tools: BLOCK_ONLY_HIGH or BLOCK_NONE.

Two block events to handle:
  1. Prompt blocked (prompt_feedback.block_reason set) — no candidates returned.
  2. Response blocked (candidate.finish_reason == SAFETY) — generation stopped mid-way.
"""

import os

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"

# Per-category safety settings — tune per product surface.
SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    ),
]


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def ask_safely(client, prompt, safety_settings=None, max_output_tokens=256):
    """
    Production-safe generate_content: never assumes .text always exists.

    Checks three cases in order:
      1. Prompt blocked — no candidates, block_reason set in prompt_feedback.
      2. Response blocked — candidate exists but finish_reason == SAFETY.
      3. Normal completion — return text and finish_reason.

    Args:
        client: genai.Client instance.
        prompt: User prompt to send.
        safety_settings: List of SafetySetting objects. Defaults to SAFETY_SETTINGS.
        max_output_tokens: Cap on response length.

    Returns:
        dict with keys: status ("ok" | "prompt_blocked" | "response_blocked"),
                        text (str or None), reason (str or None),
                        safety_ratings (list or None)
    """
    if safety_settings is None:
        safety_settings = SAFETY_SETTINGS

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            safety_settings=safety_settings,
            max_output_tokens=max_output_tokens,
        ),
    )

    # Case 1: prompt itself was blocked.
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        return {
            "status": "prompt_blocked",
            "text": None,
            "reason": str(response.prompt_feedback.block_reason),
            "safety_ratings": None,
        }

    candidate = response.candidates[0]

    # Case 2: response blocked by safety filters.
    if candidate.finish_reason == types.FinishReason.SAFETY:
        ratings = [
            {"category": str(r.category), "probability": str(r.probability)}
            for r in (candidate.safety_ratings or [])
        ]
        return {
            "status": "response_blocked",
            "text": None,
            "reason": "SAFETY",
            "safety_ratings": ratings,
        }

    # Case 3: normal completion.
    return {
        "status": "ok",
        "text": response.text,
        "reason": str(candidate.finish_reason),
        "safety_ratings": None,
    }


def run_safety():
    """Entry point for the safety settings demo."""
    client = create_client()

    test_prompts = [
        # Benign — should pass cleanly.
        "Give one tip for writing polite code review comments.",
        # Edgy but tests strict BLOCK_LOW_AND_ABOVE threshold handling.
        "Describe, in vivid insulting language, a terrible coworker.",
    ]

    print("=== Safety Settings Demo ===")
    print("Thresholds: HARASSMENT/HATE_SPEECH/SEXUALLY_EXPLICIT = BLOCK_LOW_AND_ABOVE,")
    print("            DANGEROUS_CONTENT = BLOCK_MEDIUM_AND_ABOVE\n")

    for prompt in test_prompts:
        print(f"Prompt: {prompt}")
        result = ask_safely(client, prompt)

        if result["status"] == "prompt_blocked":
            print(f"  PROMPT BLOCKED. Reason: {result['reason']}")
        elif result["status"] == "response_blocked":
            print("  RESPONSE BLOCKED by safety filters. Ratings:")
            for rating in result["safety_ratings"] or []:
                print(f"    {rating['category']}: probability={rating['probability']}")
        else:
            print(f"  OK (finish_reason={result['reason']})")
            print(f"  Answer: {result['text']}")
        print()

    print(
        "Key pattern: always check prompt_feedback.block_reason and "
        "candidate.finish_reason before accessing response.text — "
        "accessing .text on a blocked response raises an exception."
    )
