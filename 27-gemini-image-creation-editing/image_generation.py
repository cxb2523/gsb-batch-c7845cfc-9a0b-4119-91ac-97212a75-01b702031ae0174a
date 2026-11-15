# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Gemini native image generation + conversational editing.
Uses gemini-2.5-flash-image which retains conversation context, enabling
iterative edits with plain-language instructions — something Imagen cannot do.
"""

import os

from google import genai
from google.genai import types

# Gemini native image gen — unlike Imagen it keeps conversation context
# so you can iteratively EDIT the image with plain-language follow-up turns.
MODEL = "gemini-2.5-flash-image"


def get_client(project_id: str, location: str = "us-central1") -> genai.Client:
    """Create a Vertex AI Gen AI client."""
    return genai.Client(vertexai=True, project=project_id, location=location)


def create_chat(client: genai.Client) -> object:
    """Create a chat session configured for image output.

    response_modalities=["TEXT", "IMAGE"] is required to receive image parts.
    The chat object maintains turn history so follow-up edits reference the
    previously generated image automatically.
    """
    return client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
        ),
    )


def save_image_parts(response, out_dir: str, filename: str) -> str | None:
    """Extract the first inline image from a Gemini response and save as PNG.

    Args:
        response: Response object from chat.send_message().
        out_dir: Directory to write the PNG into (created if missing).
        filename: Target filename (e.g. "image_v1.png").

    Returns:
        Absolute path of the saved file, or None if no image part found.
    """
    os.makedirs(out_dir, exist_ok=True)
    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            path = os.path.join(out_dir, filename)
            with open(path, "wb") as f:
                f.write(part.inline_data.data)
            size = len(part.inline_data.data)
            print(f"  saved {path}  ({size:,} bytes)")
            return path
        if part.text:
            print(f"  model text: {part.text[:200]}")
    print("  WARNING: no image part found in response")
    return None


def generate_image(chat, prompt: str, out_dir: str, filename: str) -> str | None:
    """Send the initial generation prompt and save the resulting image.

    Cost: Gemini image output billed per image token (~$0.039/image at
    ~1290 output tokens on gemini-2.5-flash-image).

    Args:
        chat: Chat session created by create_chat().
        prompt: Initial text-to-image generation instruction.
        out_dir: Output directory.
        filename: PNG filename for saved image.

    Returns:
        Path to saved image, or None.
    """
    print(f"Generating: {prompt[:80]}...")
    response = chat.send_message(prompt)
    return save_image_parts(response, out_dir, filename)


def edit_image(chat, edit_instruction: str, out_dir: str, filename: str) -> str | None:
    """Send a conversational edit instruction on the existing image.

    No masks, no re-prompting from scratch — just describe the change.
    The chat history keeps the original image in context.

    Args:
        chat: The SAME chat session used for generate_image().
        edit_instruction: Plain-language edit instruction.
        out_dir: Output directory.
        filename: PNG filename for the edited image.

    Returns:
        Path to saved image, or None.
    """
    print(f"Editing  : {edit_instruction[:80]}...")
    response = chat.send_message(edit_instruction)
    return save_image_parts(response, out_dir, filename)
