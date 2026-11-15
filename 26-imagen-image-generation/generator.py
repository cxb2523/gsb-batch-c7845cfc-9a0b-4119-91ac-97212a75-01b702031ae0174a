# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Imagen generation functions — core image generation with default config.
Wraps the Gen AI SDK client for text-to-image using imagen-4.0-fast-generate-001.
"""

import os

from google import genai
from google.genai import types

MODEL = "imagen-4.0-fast-generate-001"


def get_client(project_id: str, location: str = "us-central1") -> genai.Client:
    """Create a Vertex AI Gen AI client."""
    return genai.Client(vertexai=True, project=project_id, location=location)


def generate_image(
    client: genai.Client,
    prompt: str,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
    model: str = MODEL,
) -> list:
    """Generate images with Imagen using default config.

    Args:
        client: Gen AI client (vertexai=True).
        prompt: Text description of the image to generate.
        number_of_images: How many images to generate (1-4, billed per image).
        aspect_ratio: "1:1" | "16:9" | "9:16" | "4:3" | "3:4".
        model: Imagen model ID. Fast tier (~$0.02/image) used by default.

    Returns:
        List of generated image objects with .image.image_bytes.
    """
    print(f"Model  : {model}")
    print(f"Prompt : {prompt}")
    print(f"Config : {number_of_images} image(s), aspect_ratio={aspect_ratio}\n")

    response = client.models.generate_images(
        model=model,
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=number_of_images,
            aspect_ratio=aspect_ratio,
        ),
    )
    return response.generated_images


def save_images(generated_images: list, out_dir: str, prefix: str = "imagen") -> list[str]:
    """Save generated image bytes to disk as PNG files.

    Args:
        generated_images: List returned by generate_image().
        out_dir: Directory to write PNG files into (created if missing).
        prefix: Filename prefix for saved files.

    Returns:
        List of absolute file paths written.
    """
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i, generated in enumerate(generated_images):
        path = os.path.join(out_dir, f"{prefix}_{i + 1}.png")
        with open(path, "wb") as f:
            f.write(generated.image.image_bytes)
        size = len(generated.image.image_bytes)
        print(f"Saved : {path}  ({size:,} bytes)")
        paths.append(path)
    return paths
