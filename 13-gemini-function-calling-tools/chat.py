# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Multi-turn chat with system instructions.

Covers two complementary patterns:
  SYSTEM INSTRUCTION: Sets persistent behavior — persona, tone, output format,
  guardrails. Sent with higher authority than user turns; the right place for
  rules you don't want users to override. Shown with and without to isolate effect.

  MULTI-TURN CHAT (client.chats): Stateful session — the SDK accumulates history
  client-side and replays the full conversation on every turn. The model itself
  is stateless; history is the memory. You pay for the growing history as input
  tokens on every call.

Use cases: assistants, tutoring bots, follow-up Q&A, context-aware pipelines.
"""

import os

from google import genai
from google.genai import types

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"

# System instruction for the structured-output comparison demo.
CONSULTANT_SYSTEM_INSTRUCTION = (
    "You are a senior GCP cost-optimization consultant. "
    "Rules: answer ONLY as a markdown table with columns "
    "'Recommendation' and 'Est. Savings Impact (High/Med/Low)'. "
    "Maximum 3 rows. No prose before or after the table."
)

# System instruction for the multi-turn chat session.
CHAT_SYSTEM_INSTRUCTION = (
    "You are a concise GCP expert. Max 2 sentences per answer."
)

PROMPT = "How can I reduce my BigQuery bill?"


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def ask_with_system_instruction(client, prompt, system_instruction=None):
    """
    Sends a single prompt with an optional system instruction.

    Call once without and once with the same prompt to isolate the effect
    of the instruction on persona, tone, and output format.

    Args:
        client: genai.Client instance.
        prompt: User-facing question.
        system_instruction: Optional str to set model behavior/persona.

    Returns:
        str: Model response text.
    """
    config = types.GenerateContentConfig(
        max_output_tokens=512,
        system_instruction=system_instruction,
    )
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=config,
    )
    return response.text


def run_multi_turn_chat(client):
    """
    Demonstrates a stateful multi-turn conversation via client.chats.

    Each call to chat.send_message():
      1. Appends the user turn to the session history.
      2. Sends the FULL accumulated history to the model.
      3. Appends the model turn to the session history.

    Responses in later turns can reference earlier turns because the full
    conversation context is replayed. Cost grows with every turn (input tokens
    = all prior turns + new message).

    Args:
        client: genai.Client instance.

    Returns:
        list of dicts with keys: turn, user, model
    """
    chat = client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            max_output_tokens=512,
            system_instruction=CHAT_SYSTEM_INSTRUCTION,
        ),
    )

    turns = [
        "I want to run a Python API on GCP with scale-to-zero. What service?",
        "What's the max request timeout there?",           # relies on turn 1
        "And how do I give it access to a BigQuery dataset?",  # relies on turns 1-2
    ]

    history = []
    for i, user_msg in enumerate(turns, start=1):
        response = chat.send_message(user_msg)
        history.append({"turn": i, "user": user_msg, "model": response.text})

    return chat, history


def run_chat():
    """Entry point for the system instruction + multi-turn chat demo."""
    client = create_client()

    # --- System instruction comparison ---
    print(f"Prompt: {PROMPT}\n")

    print("=== WITHOUT system instruction ===")
    print(ask_with_system_instruction(client, PROMPT))

    print("\n=== WITH system instruction (consultant persona, table-only) ===")
    print(ask_with_system_instruction(client, PROMPT, CONSULTANT_SYSTEM_INSTRUCTION))

    print(
        "\nNote: identical prompt — only system_instruction changed the persona "
        "and output format."
    )

    # --- Multi-turn chat ---
    print("\n=== Multi-turn Chat ===")
    chat, history = run_multi_turn_chat(client)

    for entry in history:
        print(f"\n[Turn {entry['turn']}] User  : {entry['user']}")
        print(f"[Turn {entry['turn']}] Model : {entry['model']}")

    print("\n=== Chat History (roles + first 60 chars) ===")
    for content in chat.get_history():
        text = (content.parts[0].text or "").replace("\n", " ")
        print(f"[{content.role:5}] {text[:60]}...")

    print(
        "\nNote: get_history() returns all Content objects — use this to persist "
        "conversations or trim old turns to manage input token growth."
    )
