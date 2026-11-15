# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Token counting and usage metadata inspection.

Two-phase pattern:
  1. Pre-flight: count_tokens BEFORE sending — reject oversized requests early,
     verify context-window fit, and gate on cost thresholds.
  2. Post-flight: usage_metadata AFTER the call — the ground truth for billing
     (prompt, candidate, thinking, and total token counts).

Gemini 2.5 models produce thinking tokens (billed as output) separately from
candidate tokens — both show up in usage_metadata.
"""

import os

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def count_tokens_preflight(client, prompt):
    """
    Counts tokens for a prompt BEFORE sending it to the model.

    Use this to:
    - Reject requests that exceed your cost budget.
    - Verify a prompt fits the model's context window.
    - Build per-request cost estimates before committing the API call.

    Args:
        client: genai.Client instance.
        prompt: Text prompt to evaluate.

    Returns:
        int: Estimated total tokens for the prompt.
    """
    count = client.models.count_tokens(model=MODEL, contents=prompt)
    return count.total_tokens


def generate_and_inspect_usage(client, prompt, max_output_tokens=1024):
    """
    Sends the prompt and returns both the response text and the full
    usage_metadata breakdown.

    Thinking tokens (thoughts_token_count) are non-zero for Gemini 2.5 models
    when the model reasons through a problem. They are billed as output tokens.

    Args:
        client: genai.Client instance.
        prompt: Text prompt to send.
        max_output_tokens: Cap on generated tokens.

    Returns:
        dict with keys: text, prompt_tokens, candidate_tokens,
                        thinking_tokens, total_tokens
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=max_output_tokens),
    )
    usage = response.usage_metadata
    return {
        "text": response.text,
        "prompt_tokens": usage.prompt_token_count,
        "candidate_tokens": usage.candidates_token_count,
        "thinking_tokens": usage.thoughts_token_count,
        "total_tokens": usage.total_token_count,
    }


def run_token_counting():
    """Entry point for the token counting and usage metadata demo."""
    client = create_client()

    prompt = (
        "Explain the difference between BigQuery on-demand and "
        "capacity (slot-based) pricing in three short bullet points."
    )

    # --- Pre-flight ---
    print("=== Pre-flight Token Count ===")
    estimated = count_tokens_preflight(client, prompt)
    print(f"Prompt tokens (estimated before call): {estimated}")

    # --- Call + post-flight ---
    print("\n=== Response ===")
    result = generate_and_inspect_usage(client, prompt)
    print(result["text"])

    print("=== usage_metadata (actual billed usage) ===")
    print(f"Prompt tokens     : {result['prompt_tokens']}")
    print(f"Candidate tokens  : {result['candidate_tokens']}")
    print(f"Thinking tokens   : {result['thinking_tokens']}")
    print(f"Total tokens      : {result['total_tokens']}")
    print(
        "\nNote: thinking tokens are billed as output — watch thoughts_token_count "
        "in production to avoid surprise cost spikes on reasoning-heavy prompts."
    )
