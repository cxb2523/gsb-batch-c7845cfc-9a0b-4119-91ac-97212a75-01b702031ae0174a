# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Basic Gemini text generation and generation config patterns.
Covers: single synchronous call, GenerateContentConfig parameters (temperature,
top_p, max_output_tokens, stop_sequences, seed, candidate_count).
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


def basic_generation(client, prompt="In one sentence, what is Vertex AI?"):
    """
    Single synchronous generate_content call — the canonical hello-Gemini pattern.
    Use this as a baseline to verify auth, project setup, and model access.
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )
    return response.text


def generation_with_config(client, prompt="Suggest a name for a FinOps dashboard product. Name only."):
    """
    Demonstrates the core sampling and control knobs via GenerateContentConfig.

    Key parameters:
    - temperature : randomness of sampling. 0 = near-deterministic, ~1 = creative.
    - top_p       : nucleus sampling cutoff. Lower = safer picks.
    - max_output_tokens : hard cap on generated tokens — primary cost guardrail.
    - stop_sequences    : generation halts when any string is produced.
    - seed        : best-effort reproducibility across runs.
    - candidate_count   : number of independent completions returned per call.
    """
    config = types.GenerateContentConfig(
        temperature=0.9,
        top_p=0.95,
        max_output_tokens=512,
        stop_sequences=["\n\n", "END"],
        seed=42,
        candidate_count=2,
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=config,
    )

    candidates = []
    for candidate in response.candidates:
        text = candidate.content.parts[0].text if candidate.content.parts else ""
        candidates.append({
            "finish_reason": str(candidate.finish_reason),
            "text": text.strip(),
        })

    # Second run with same seed — outputs are typically identical (reproducibility).
    response2 = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=config,
    )
    seed_repro_text = response2.candidates[0].content.parts[0].text.strip()

    return {
        "candidates": candidates,
        "seed_repro_text": seed_repro_text,
    }


def run_basic_generation():
    """Entry point for the basic generation demo."""
    client = create_client()
    prompt = "In one sentence, what is Vertex AI?"

    print("=== Basic Generation ===")
    print(f"Prompt: {prompt}")
    result = basic_generation(client, prompt)
    print(f"\nResponse:\n{result}")


def run_generation_config():
    """Entry point for the generation config demo."""
    client = create_client()
    prompt = "Suggest a name for a FinOps dashboard product. Name only."

    print("\n=== Generation Config ===")
    print(f"Prompt: {prompt}\n")

    result = generation_with_config(client, prompt)

    print("Candidates:")
    for i, c in enumerate(result["candidates"], start=1):
        print(f"  [{i}] finish_reason={c['finish_reason']} | {c['text']}")

    print(f"\nSame seed, second run (candidate 1): {result['seed_repro_text']}")
    print("Same seed + same config => outputs are typically identical.")
