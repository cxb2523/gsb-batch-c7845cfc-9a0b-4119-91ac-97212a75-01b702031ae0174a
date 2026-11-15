# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Streaming response from Gemini via generate_content_stream.
Tokens are printed as they arrive instead of waiting for the full completion —
the right pattern for chat UIs and long outputs where time-to-first-token matters.
"""

import os
import time

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def stream_response(client, prompt, max_output_tokens=512):
    """
    Streams a Gemini response chunk-by-chunk.

    Returns timing metadata: time-to-first-chunk and total elapsed seconds.
    Chunks are printed inline as they arrive (end="" + flush=True).

    Args:
        client: genai.Client instance.
        prompt: Text prompt to send.
        max_output_tokens: Cap on generated tokens.

    Returns:
        dict with keys: first_chunk_latency, total_latency
    """
    start = time.time()
    first_chunk_at = None

    stream = client.models.generate_content_stream(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=max_output_tokens),
    )

    for chunk in stream:
        if first_chunk_at is None:
            first_chunk_at = time.time() - start
        if chunk.text:
            print(chunk.text, end="", flush=True)

    total = time.time() - start
    print()  # newline after streamed output
    return {
        "first_chunk_latency": first_chunk_at,
        "total_latency": total,
    }


def run_streaming():
    """Entry point for the streaming demo."""
    client = create_client()

    prompt = (
        "Write a short 5-step checklist for deploying a containerized "
        "app to Cloud Run. One line per step."
    )

    print("=== Streaming Response (chunks printed as they arrive) ===\n")
    timing = stream_response(client, prompt, max_output_tokens=512)

    print(f"\n=== Timing ===")
    print(f"Time to first chunk : {timing['first_chunk_latency']:.2f}s")
    print(f"Total time          : {timing['total_latency']:.2f}s")
    print(
        "\nNote: streaming reduces perceived latency — users see output immediately "
        "instead of waiting for the full response."
    )
