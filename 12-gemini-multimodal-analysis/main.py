# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 12: Gemini Multimodal Analysis
Demonstrates image, PDF document, and video/audio analysis in sequence using
the same Part.from_uri / Part.from_bytes pattern across all modalities.
"""

from image_analysis import run_image_analysis
from document_analysis import run_document_analysis
from media_analysis import run_media_analysis


def separator(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def main():
    separator("1. Image Analysis (GCS URI + local bytes)")
    run_image_analysis()

    separator("2. PDF Document Analysis (summarize + extract)")
    run_document_analysis()

    separator("3. Video & Audio Analysis (describe + transcribe)")
    run_media_analysis()

    print("\nAll multimodal patterns complete.")


if __name__ == "__main__":
    main()
