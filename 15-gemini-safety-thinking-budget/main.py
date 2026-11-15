# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 15: Gemini Safety Settings & Thinking Budget
Runs two patterns in sequence:
  1. Safety settings — per-category harm thresholds + blocked-response handling
  2. Thinking budget — compare thinking OFF / dynamic / visible-thoughts modes
"""

from safety import run_safety
from thinking import run_thinking


def separator(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def main():
    separator("1. Safety Settings & Blocked-Response Handling")
    run_safety()

    separator("2. Thinking Budget Control (Gemini 2.5)")
    run_thinking()

    print("\nAll safety and thinking budget patterns complete.")


if __name__ == "__main__":
    main()
