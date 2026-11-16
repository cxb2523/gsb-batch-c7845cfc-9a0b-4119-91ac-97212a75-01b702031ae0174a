"""
Pytest configuration for the cf-clearance-scraper tests.

The repository root holds ``main.py`` (there is no installable package), so this
file only exists to make the project root importable from the tests in
``tests/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
