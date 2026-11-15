# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Imagen config option variants — demonstrates key GenerateImagesConfig knobs:
aspect_ratio, negative_prompt, person_generation, seed, and add_watermark.
"""

import os

from google import genai
from google.genai import types

MODEL = "imagen-4.0-fast-generate-001"


def generate_portrait_no_people(
    client: genai.Client,
    out_dir: str,
    model: str = MODEL,
) -> list[str]:
    """Demo 1: portrait aspect ratio + negative_prompt + no people.

    Useful for brand-safe product imagery where you want a tall/mobile
    crop and need to exclude specific visual elements.
    Cost: ~$0.02 (1 image, fast tier).
    """
    print("Demo 1: 9:16 portrait, negative_prompt, person_generation=dont_allow")
    response = client.models.generate_images(
        model=model,
        prompt="Minimal product shot of a smart speaker on a wooden desk",
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="9:16",
            negative_prompt="text, watermark, logo, clutter, hands",
            # "dont_allow" = brand-safe; options: dont_allow | allow_adult | allow_all
            person_generation="dont_allow",
        ),
    )
    return _save(response.generated_images, out_dir, "config_portrait")


def generate_seeded_deterministic(
    client: genai.Client,
    out_dir: str,
    seed: int = 42,
    model: str = MODEL,
) -> list[str]:
    """Demo 2: reproducible output via seed + add_watermark=False.

    NOTE: seed requires add_watermark=False — the API rejects the combination
    of seed + SynthID watermark. Identical model + prompt + seed => identical
    image, which is useful for regression-testing prompt pipelines or
    regenerating an asset deterministically.
    Cost: ~$0.02 (1 image, fast tier).
    """
    print(f"\nDemo 2: deterministic generation (seed={seed}, add_watermark=False)")
    response = client.models.generate_images(
        model=model,
        prompt="Flat vector icon of a rocket launching from a cloud",
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="1:1",
            seed=seed,
            add_watermark=False,        # mandatory when using seed
            output_mime_type="image/png",
        ),
    )
    return _save(response.generated_images, out_dir, "config_seeded")


def _save(generated_images, out_dir: str, prefix: str) -> list[str]:
    """Internal helper: save image bytes to disk."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for i, gen in enumerate(generated_images):
        path = os.path.join(out_dir, f"{prefix}_{i + 1}.png")
        with open(path, "wb") as f:
            f.write(gen.image.image_bytes)
        print(f"  saved {path}")
        paths.append(path)
    return paths
