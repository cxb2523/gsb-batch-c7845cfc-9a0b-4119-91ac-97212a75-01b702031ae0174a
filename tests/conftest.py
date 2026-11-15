import os
import sys

import pytest

# Make ``src`` importable without packaging the application.
SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture
def workspace(tmp_path):
    """Provide an input directory and an output path inside tmp_path."""
    input_dir = tmp_path / 'input'
    input_dir.mkdir()
    return tmp_path, input_dir
