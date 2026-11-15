# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Grounding with Google Search — live web retrieval for Gemini answers.

Instead of relying solely on training data, Gemini issues Google Search
queries, injects the results as context, and cites sources in the answer.

Use for: current events, recent product releases, live pricing, anything that
changes faster than the model's training cutoff.

Billing note: grounded requests are billed at a different rate than plain calls
(check Vertex AI pricing page for current Search grounding rates).
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


def grounded_generate(client, prompt, max_output_tokens=512):
    """
    Generates a grounded answer with live Google Search results.

    The model decides when to issue searches based on the prompt. Results
    are injected as grounding context automatically — you don't control
    which queries are issued, but you can inspect them in the response metadata.

    Args:
        client: genai.Client instance.
        prompt: Question or task for the model.
        max_output_tokens: Cap on response length.

    Returns:
        dict with keys:
            text           — model answer with grounded claims
            search_queries — list of queries the model issued
            sources        — list of dicts with title and uri
    """
    search_tool = types.Tool(google_search=types.GoogleSearch())

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[search_tool],
            max_output_tokens=max_output_tokens,
        ),
    )

    search_queries = []
    sources = []

    metadata = response.candidates[0].grounding_metadata
    if metadata:
        search_queries = list(metadata.web_search_queries or [])
        for chunk in metadata.grounding_chunks or []:
            if chunk.web:
                sources.append({"title": chunk.web.title, "uri": chunk.web.uri})

    return {
        "text": response.text,
        "search_queries": search_queries,
        "sources": sources,
    }


def run_grounding():
    """Entry point for the Google Search grounding demo."""
    client = create_client()

    prompt = (
        "What is the most recent generally-available Gemini model on "
        "Vertex AI, and when was it released? Answer in 2 sentences."
    )

    print("=== Grounded Answer ===")
    result = grounded_generate(client, prompt)
    print(result["text"])

    if result["search_queries"]:
        print("\n=== Search Queries Used by the Model ===")
        for q in result["search_queries"]:
            print(f"- {q}")
    else:
        print("\n(No search queries — model answered from training data.)")

    if result["sources"]:
        print("\n=== Sources ===")
        for i, src in enumerate(result["sources"], start=1):
            print(f"[{i}] {src['title']} -> {src['uri']}")
    else:
        print("(No grounding sources returned.)")

    print(
        "\nNote: always surface citations in user-facing apps — "
        "often a usage policy requirement for grounded responses."
    )
