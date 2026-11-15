#!/usr/bin/env bash
set -euo pipefail

# Project 29: Data Format Processing
# This project is fully local — no GCP APIs required.
# The only setup needed is a Python virtual environment.

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"

echo "Project 29: Data Format Processing"
echo "======================================"
echo "This project runs entirely locally — no GCP APIs are called."
echo ""
echo "Run:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
echo ""
echo "Output files will be written to output/"
