"""Pytest bootstrap.

Makes the repository root importable so the tests can do
``from dags import weather_pipeline``. Importing the package only executes
its (empty) namespace ``__init__``; the Airflow-dependent DAG module is never
imported, which keeps these unit tests runnable without an Airflow install.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
