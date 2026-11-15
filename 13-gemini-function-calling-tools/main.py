# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 13: Gemini Function Calling & Tools
Runs three patterns in sequence:
  1. Function calling — automatic SDK loop + manual FunctionDeclaration flow
  2. Structured output — Pydantic schema-constrained JSON extraction
  3. Multi-turn chat — stateful session with system instructions
"""

from function_calling import run_function_calling
from structured_output import run_structured_output
from chat import run_chat


def separator(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def main():
    separator("1. Function Calling (automatic + manual flows)")
    run_function_calling()

    separator("2. Structured Output (Pydantic schema enforcement)")
    run_structured_output()

    separator("3. System Instructions + Multi-turn Chat")
    run_chat()

    print("\nAll function calling and tool patterns complete.")


if __name__ == "__main__":
    main()
