# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 39 — Vertex AI Model Catalog Explorer
Lists all publisher/foundation models available to the project, filters by
common capability categories, and prints a formatted catalog table with counts.
"""

import os

from model_catalog import filter_models_by_capability, list_publisher_models

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# Capability buckets to filter and count
CAPABILITY_FILTERS = ["gemini", "embed", "imagen", "code", "vision"]


def print_full_catalog(models: list[dict]) -> None:
    """Prints the full formatted model catalog table."""
    print(f"Project : {PROJECT_ID}")
    print(f"Location: {LOCATION}")
    print("-" * 100)
    header = f"{'MODEL NAME':<55} {'DISPLAY NAME':<25} {'ACTIONS':<20}"
    print(header)
    print("=" * 100)
    for m in models:
        name = m["name"][:54]
        display = m["display_name"][:24]
        actions = m["actions"][:19]
        print(f"{name:<55} {display:<25} {actions:<20}")
    print("=" * 100)
    print(f"Total models listed : {len(models)}")


def print_capability_breakdown(models: list[dict]) -> None:
    """Filters models by capability keywords and prints counts."""
    print("\nCAPABILITY BREAKDOWN")
    print("-" * 50)
    for cap in CAPABILITY_FILTERS:
        filtered = filter_models_by_capability(models, cap)
        print(f"  {cap:<15} : {len(filtered)} model(s)")


def print_version_tip(models: list[dict]) -> None:
    """Warns about floating aliases and prints gemini model names as examples."""
    gemini_models = filter_models_by_capability(models, "gemini")
    print("\nPRODUCTION PINNING TIP")
    print("-" * 80)
    print("Pin exact model versions in production configs — floating aliases")
    print("(e.g. 'gemini-pro') may silently update to a new model version.")
    if gemini_models:
        print("\nGemini models currently available (pin these exact names):")
        for m in gemini_models[:10]:
            print(f"  {m['name']}")
        if len(gemini_models) > 10:
            print(f"  ... and {len(gemini_models) - 10} more")
    print()
    print("NOTE: This is a read-only listing call — no cost is incurred.")


def main():
    print("\n")
    print("#" * 80)
    print(f"  VERTEX AI MODEL CATALOG EXPLORER — project: {PROJECT_ID}")
    print("#" * 80)
    print()

    # Fetch full catalog
    models = list_publisher_models(PROJECT_ID, LOCATION)

    # Print formatted catalog table
    print_full_catalog(models)

    # Capability breakdown
    print_capability_breakdown(models)

    # Production version pinning tip
    print_version_tip(models)


if __name__ == "__main__":
    main()
