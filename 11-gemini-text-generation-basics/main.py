# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 11: Gemini Text Generation Basics
Runs all four patterns in sequence:
  1. Basic generate_content call
  2. GenerateContentConfig (temperature, top_p, seed, candidate_count, etc.)
  3. Streaming response (time-to-first-chunk)
  4. Token counting — pre-flight count + post-flight usage_metadata
"""

from generation import run_basic_generation, run_generation_config
from streaming import run_streaming
from tokens import run_token_counting


def separator(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def main():
    separator("1. Basic Generation")
    run_basic_generation()

    separator("2. Generation Config (temperature / seed / candidate_count)")
    run_generation_config()

    separator("3. Streaming Response")
    run_streaming()

    separator("4. Token Counting & Usage Metadata")
    run_token_counting()

    print("\nAll patterns complete.")


if __name__ == "__main__":
    main()
