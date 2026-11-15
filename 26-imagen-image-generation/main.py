# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 26: Imagen Image Generation
Runs default generation, then demonstrates key config options (portrait,
negative_prompt, deterministic seed). All images saved to output/.

Cost estimate: ~$0.06 total (3 images at $0.02 each on the fast tier).
"""

import os
from pathlib import Path

from generator import get_client, generate_image, save_images
from config_options import generate_portrait_no_people, generate_seeded_deterministic

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

OUT_DIR = str(Path(__file__).parent / "output")

DEFAULT_PROMPT = (
    "Isometric illustration of a cloud data center with glowing network "
    "links, clean vector style, dark background, teal and orange accents"
)


def main():
    print("=" * 60)
    print("Project 26: Imagen Image Generation")
    print("=" * 60)
    print(f"Project : {PROJECT_ID}")
    print(f"Location: {LOCATION}")
    print(f"Output  : {OUT_DIR}\n")

    client = get_client(PROJECT_ID, LOCATION)

    # --- Step 1: Default generation (16:9, 1 image) --------------------------
    print("--- Step 1: Default generation (16:9, fast tier) ---")
    images = generate_image(
        client=client,
        prompt=DEFAULT_PROMPT,
        number_of_images=1,
        aspect_ratio="16:9",
    )
    save_images(images, OUT_DIR, prefix="imagen_default")
    print()

    # --- Step 2: Config option variants --------------------------------------
    print("--- Step 2: Config option variants ---")
    generate_portrait_no_people(client, OUT_DIR)
    generate_seeded_deterministic(client, OUT_DIR, seed=42)

    print()
    print("=" * 60)
    print("Done. Images written to output/")
    print("Cost note: 3 images on fast tier ~= $0.06 total")
    print("=" * 60)


if __name__ == "__main__":
    main()
