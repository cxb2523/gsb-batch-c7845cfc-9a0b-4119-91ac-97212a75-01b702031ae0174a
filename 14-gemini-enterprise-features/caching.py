# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Context caching — cache a large document once, query it many times cheaply.

Cached input tokens are billed at a steep discount compared to regular input
tokens. The pattern: pay full price ONCE to create the cache, then pay only
the small incremental prompt tokens on each subsequent query.

Best fit: many requests sharing the same large context (documents, codebases,
reference specs, system prompts). Avoid if each request has a unique context.

Important constraints:
- Minimum cached token threshold: 1,024 tokens for 2.5 Flash, 2,048 for 2.5 Pro.
- Cache storage is billed per token-hour — set a short TTL for demos.
- Always delete caches when done (or set TTL) to stop storage billing.
"""

import os
import datetime

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"

# Large public PDF — big enough to exceed the minimum cache token threshold.
PDF_URI = "gs://cloud-samples-data/generative-ai/pdf/2403.05530.pdf"


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def create_cache(client, pdf_uri=PDF_URI, ttl_minutes=10):
    """
    Creates a cached content object from a PDF stored in GCS.

    The PDF tokens are sent once and stored server-side. The returned cache
    name is used in subsequent generate_content calls via cached_content=.

    Args:
        client: genai.Client instance.
        pdf_uri: GCS URI of the PDF to cache.
        ttl_minutes: How long to keep the cache alive (storage billing runs
                     until TTL expires or cache is deleted).

    Returns:
        The cache object (contains .name, .usage_metadata.total_token_count).
    """
    ttl_seconds = int(datetime.timedelta(minutes=ttl_minutes).total_seconds())
    cache = client.caches.create(
        model=MODEL,
        config=types.CreateCachedContentConfig(
            display_name="sample-pdf-cache",
            system_instruction="You answer questions about the attached paper, briefly.",
            contents=[
                types.Part.from_uri(file_uri=pdf_uri, mime_type="application/pdf")
            ],
            ttl=f"{ttl_seconds}s",
        ),
    )
    return cache


def query_cache(client, cache_name, question, max_output_tokens=256):
    """
    Queries the cached context with a new question.

    Only the small question is sent as new input; the large PDF context
    is pulled from the cache server-side at the discounted token rate.

    Args:
        client: genai.Client instance.
        cache_name: Name of the existing cache (from create_cache).
        question: Question to ask about the cached document.
        max_output_tokens: Cap on response length.

    Returns:
        dict with keys: text, prompt_token_count, cached_token_count
    """
    response = client.models.generate_content(
        model=MODEL,
        contents=question,
        config=types.GenerateContentConfig(
            cached_content=cache_name,
            max_output_tokens=max_output_tokens,
        ),
    )
    usage = response.usage_metadata
    return {
        "text": response.text,
        "prompt_token_count": usage.prompt_token_count,
        "cached_token_count": usage.cached_content_token_count,
    }


def delete_cache(client, cache_name):
    """Deletes a cache to stop storage billing immediately."""
    client.caches.delete(name=cache_name)


def run_caching():
    """Entry point for the context caching demo."""
    client = create_client()

    print("=== Creating Cached Content (PDF cached for 10 minutes) ===")
    cache = create_cache(client)
    print(f"Cache name   : {cache.name}")
    print(f"Cached tokens: {cache.usage_metadata.total_token_count}")

    questions = [
        "What model family does this paper introduce? One sentence.",
        "What is the headline context window size discussed? One sentence.",
    ]

    try:
        for q in questions:
            result = query_cache(client, cache.name, q)
            print(f"\nQ: {q}")
            print(f"A: {result['text']}")
            print(f"   prompt_token_count        : {result['prompt_token_count']}")
            print(f"   cached_content_token_count: {result['cached_token_count']}")
            print(f"   full-price new input tokens: "
                  f"{result['prompt_token_count'] - result['cached_token_count']}")
    finally:
        delete_cache(client, cache.name)
        print(f"\nDeleted cache: {cache.name}")
        print(
            "Note: always clean up caches — storage is billed per token-hour "
            "until TTL expires or explicit deletion."
        )
