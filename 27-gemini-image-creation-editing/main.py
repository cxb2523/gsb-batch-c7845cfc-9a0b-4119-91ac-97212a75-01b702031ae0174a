# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 27: Gemini Image Creation and Editing
Generates an image with Gemini native image gen, edits it via conversational
turn ("make the scooter blue"), then analyses the result with Gemini multimodal.

Cost estimate: ~$0.08 for 2 image generation turns + ~$0.01 for analysis.
"""

import os
from pathlib import Path

from image_generation import get_client, create_chat, generate_image, edit_image
from image_analysis import get_analysis_client, analyze_local_image

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

OUT_DIR = str(Path(__file__).parent / "output")

INITIAL_PROMPT = (
    "Generate an image of a red vintage scooter parked in front of "
    "a small cafe, soft morning light, photorealistic."
)
EDIT_PROMPT = "Make the scooter blue, keep everything else exactly the same."


def main():
    print("=" * 60)
    print("Project 27: Gemini Image Creation and Editing")
    print("=" * 60)
    print(f"Project : {PROJECT_ID}")
    print(f"Location: {LOCATION}")
    print(f"Output  : {OUT_DIR}\n")

    # --- Step 1: Generate initial image --------------------------------------
    print("--- Step 1: Generate image (Gemini native image gen) ---")
    gen_client = get_client(PROJECT_ID, LOCATION)
    chat = create_chat(gen_client)

    v1_path = generate_image(
        chat=chat,
        prompt=INITIAL_PROMPT,
        out_dir=OUT_DIR,
        filename="gemini_scooter_v1.png",
    )
    print()

    # --- Step 2: Conversational edit -----------------------------------------
    print("--- Step 2: Edit image (conversational turn) ---")
    v2_path = edit_image(
        chat=chat,
        edit_instruction=EDIT_PROMPT,
        out_dir=OUT_DIR,
        filename="gemini_scooter_v2_blue.png",
    )
    print()

    # --- Step 3: Analyse the edited image ------------------------------------
    print("--- Step 3: Analyse the edited image with Gemini multimodal ---")
    analysis_image = v2_path or v1_path
    if analysis_image and os.path.exists(analysis_image):
        analysis_client = get_analysis_client(PROJECT_ID, LOCATION)
        results = analyze_local_image(analysis_client, analysis_image)

        print("\n=== DESCRIPTION ===")
        print(results["description"])

        print("\n=== OBJECTS (JSON) ===")
        print(results["objects_json"])

        print("\n=== TEXT IN IMAGE (OCR) ===")
        print(results["ocr_text"])
    else:
        print("  Skipping analysis — no image file available (check generation logs above).")

    print()
    print("=" * 60)
    print("Done. Compare the two scooter images in output/")
    print("  gemini_scooter_v1.png  — original red scooter")
    print("  gemini_scooter_v2_blue.png — same scene, blue scooter")
    print("=" * 60)


if __name__ == "__main__":
    main()
