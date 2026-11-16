import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    """Run every search inside a throwaway directory.

    ``output/`` and ``cache/`` are resolved relative to the working directory,
    so this keeps the repository clean and makes the written artefacts easy to
    inspect.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path
