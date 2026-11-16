"""OrbitPilot local executor skeleton.

A pure-Python, browser-free step runner. Steps are described in a small
YAML configuration and executed against an injectable page adapter. The
default adapter operates on local static HTML sample pages only — no
browser, no network.
"""

from .executor import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    render_manifest,
    run_strategy,
)
from .adapters import PageAdapter, PageError, StaticPageAdapter
from .yamlmini import load as load_yaml

__all__ = [
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "STATUS_SUCCESS",
    "render_manifest",
    "run_strategy",
    "PageAdapter",
    "PageError",
    "StaticPageAdapter",
    "load_yaml",
]
