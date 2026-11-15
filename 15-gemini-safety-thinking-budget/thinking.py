# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Thinking budget control for Gemini 2.5 models.

Gemini 2.5 models "think" before answering — reasoning happens internally and
thinking tokens are billed as OUTPUT (the most expensive token class). This
sample compares three modes to let you pick the right cost/quality trade-off:

  thinking_budget=0   : thinking OFF — cheapest + fastest, but quality drops
                         on multi-step reasoning. Fine for simple tasks.
  None (default)      : dynamic thinking — model decides how much to think
                         based on prompt difficulty. Good general default.
  include_thoughts=True : visible reasoning — thought summaries returned
                         alongside the answer. Use for debugging/explainability.

Cost note: thoughts_token_count in usage_metadata shows how many output-priced
tokens were consumed by reasoning. Set an explicit thinking_budget to cap spend
on high-volume endpoints.

Note: Gemini 2.5 Pro cannot fully disable thinking (budget=0 is Flash-only).
"""

import os
import time

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"

# A prompt with real reasoning demand — so differences across modes are visible.
REASONING_PROMPT = (
    "A Cloud Run service handles 120 req/s, each request takes 250ms, and "
    "concurrency is set to 10. Roughly how many instances are needed? "
    "Answer with the number and one line of reasoning."
)


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def run_with_thinking_config(client, label, thinking_config, max_output_tokens=2048):
    """
    Runs a generate_content call with the given thinking configuration.

    Args:
        client: genai.Client instance.
        label: Human-readable label for this mode (printed in output).
        thinking_config: types.ThinkingConfig instance or None (for default).
        max_output_tokens: Cap on output tokens — set large to accommodate
                           both thought summaries and the answer.

    Returns:
        dict with keys: label, text, latency, prompt_tokens, thinking_tokens,
                        candidate_tokens, total_tokens, response (raw object)
    """
    start = time.time()
    response = client.models.generate_content(
        model=MODEL,
        contents=REASONING_PROMPT,
        config=types.GenerateContentConfig(
            thinking_config=thinking_config,
            max_output_tokens=max_output_tokens,
        ),
    )
    elapsed = time.time() - start
    usage = response.usage_metadata

    return {
        "label": label,
        "text": response.text,
        "latency": elapsed,
        "prompt_tokens": usage.prompt_token_count,
        "thinking_tokens": usage.thoughts_token_count,
        "candidate_tokens": usage.candidates_token_count,
        "total_tokens": usage.total_token_count,
        "response": response,
    }


def extract_thought_parts(response):
    """
    Extracts visible thought summary parts from a response with include_thoughts=True.

    Thought parts have .thought=True and contain the model's reasoning summary.
    They are interleaved with regular text parts in the candidate's content.

    Args:
        response: Raw generate_content response object.

    Returns:
        list of str: text content of each thought part (may be empty).
    """
    thoughts = []
    for part in response.candidates[0].content.parts:
        if part.thought and part.text:
            thoughts.append(part.text)
    return thoughts


def run_thinking():
    """Entry point for the thinking budget demo."""
    client = create_client()

    print(f"Prompt: {REASONING_PROMPT}\n")

    # --- Mode 1: Thinking OFF ---
    result_off = run_with_thinking_config(
        client,
        "thinking_budget=0 (thinking OFF)",
        types.ThinkingConfig(thinking_budget=0),
    )

    # --- Mode 2: Default (dynamic) ---
    result_default = run_with_thinking_config(
        client,
        "default (model-chosen dynamic budget)",
        None,
    )

    # --- Mode 3: Visible thoughts ---
    result_visible = run_with_thinking_config(
        client,
        "include_thoughts=True (visible reasoning)",
        types.ThinkingConfig(include_thoughts=True),
    )

    # Print comparison
    for result in [result_off, result_default, result_visible]:
        print(f"=== {result['label']} ===")
        print(f"Answer : {result['text']}")
        print(f"Latency: {result['latency']:.2f}s")
        print(
            f"Tokens : prompt={result['prompt_tokens']} "
            f"thoughts={result['thinking_tokens']} "
            f"output={result['candidate_tokens']} "
            f"total={result['total_tokens']}"
        )
        print()

    # Show thought summaries for the visible mode.
    thoughts = extract_thought_parts(result_visible["response"])
    if thoughts:
        print("=== Visible Thought Summaries ===")
        for t in thoughts:
            print(t[:400])
            print()

    print(
        "Trade-off summary:\n"
        "  thinking_budget=0  → fastest, cheapest, weaker on multi-step reasoning\n"
        "  dynamic (default)  → model self-calibrates; watch thoughts_token_count\n"
        "  include_thoughts   → debuggable reasoning; higher token cost\n"
        "  Explicit budget    → predictable cost cap for high-volume endpoints"
    )
