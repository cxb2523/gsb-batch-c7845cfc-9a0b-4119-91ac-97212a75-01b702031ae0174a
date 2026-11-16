"""Test configuration.

The crawler imports ``mlx_lm`` at module import time. Real MLX is not
available (or required) on the test machine, so install lightweight stub
modules before any project import. The Markdown generation path is replaced
by test doubles in individual tests; image downloading and link rewriting
still run through the production code.
"""

from __future__ import annotations

import sys
import types

# Register shared fixtures (local HTTP server, sample images) from support.py.
pytest_plugins = ["tests.support"]


def _install_playwright_compat() -> None:
    """Support environments where ``pyee`` predates the ``pyee.asyncio`` module.

    Older ``pyee`` releases expose the same class as ``pyee._asyncio``;
    Playwright imports the public path, so bridge it for test runs.
    """
    try:
        import pyee.asyncio  # noqa: F401
    except Exception:  # noqa: BLE001 - module layout varies by version
        try:
            import pyee._asyncio as _legacy
        except Exception:  # noqa: BLE001 - nothing to bridge
            return
        shim = types.ModuleType("pyee.asyncio")
        shim.AsyncIOEventEmitter = _legacy.AsyncIOEventEmitter
        sys.modules["pyee.asyncio"] = shim


def _install_mlx_stubs() -> None:
    if "mlx_lm" not in sys.modules:
        mlx_lm = types.ModuleType("mlx_lm")

        def _raise_missing_model(*_args, **_kwargs):
            raise RuntimeError("mlx_lm is stubbed out in tests")

        mlx_lm.load = _raise_missing_model
        mlx_lm.batch_generate = _raise_missing_model
        sys.modules["mlx_lm"] = mlx_lm

    if "mlx_vlm" not in sys.modules:
        mlx_vlm = types.ModuleType("mlx_vlm")
        prompt_utils = types.ModuleType("mlx_vlm.prompt_utils")

        def _raise_missing_vlm(*_args, **_kwargs):
            raise RuntimeError("mlx_vlm is stubbed out in tests")

        mlx_vlm.load = _raise_missing_vlm
        mlx_vlm.generate = _raise_missing_vlm
        prompt_utils.apply_chat_template = _raise_missing_vlm
        mlx_vlm.prompt_utils = prompt_utils
        sys.modules["mlx_vlm"] = mlx_vlm
        sys.modules["mlx_vlm.prompt_utils"] = prompt_utils


_install_mlx_stubs()
_install_playwright_compat()
