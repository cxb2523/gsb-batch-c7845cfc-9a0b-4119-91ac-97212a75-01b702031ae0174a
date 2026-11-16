"""Shared fixtures and local HTTP helpers for the image pipeline tests."""

from __future__ import annotations

import http.server
import struct
import threading
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

import pytest

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    body = chunk_type + data
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )


def make_png(width: int = 160, height: int = 160) -> bytes:
    """Build a valid PNG large enough to pass the minimum size check."""
    rows = []
    for y in range(height):
        row = bytearray(b"\x00")
        for x in range(width):
            row.extend(((x * 7 + y * 13) % 256, (x * 3) % 256, (y * 5) % 256))
        rows.append(bytes(row))
    raw = b"".join(rows)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, level=0))
        + _png_chunk(b"IEND", b"")
    )


VALID_PNG = make_png()


@dataclass
class Route:
    """A single canned response served by :class:`LocalImageServer`."""

    body: bytes = b""
    content_type: str = "application/octet-stream"
    status: int = 200
    delay: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)
    chunked: bool = False


class LocalImageServer:
    """Threaded HTTP server used instead of talking to the real internet."""

    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.requests: list[str] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):  # silence test output
                return

            def do_GET(self):  # noqa: N802 - stdlib naming
                path = urlsplit(self.path).path
                server.requests.append(path)
                route = server.routes.get(path)
                if route is None:
                    self.send_error(404)
                    return
                if route.delay:
                    threading.Event().wait(route.delay)
                self.send_response(route.status)
                self.send_header("Content-Type", route.content_type)
                for name, value in route.headers.items():
                    self.send_header(name, value)
                if route.chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        for start in range(0, len(route.body), 4096):
                            chunk = route.body[start : start + 4096]
                            self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                            self.wfile.write(chunk)
                            self.wfile.write(b"\r\n")
                        self.wfile.write(b"0\r\n\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    return
                if "Content-Length" not in route.headers:
                    self.send_header("Content-Length", str(len(route.body)))
                self.end_headers()
                if route.body:
                    try:
                        self.wfile.write(route.body)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        # Clients intentionally hang up early (timeouts, size limits); that is
        # expected and should not spray tracebacks into the test output.
        self._server.handle_error = lambda *_args, **_kwargs: None
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="image-test-server", daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def add(self, path: str, **kwargs) -> str:
        self.routes[path] = Route(**kwargs)
        return self.url(path)

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture()
def image_server() -> Iterator[LocalImageServer]:
    server = LocalImageServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture()
def sample_png() -> bytes:
    return VALID_PNG


def _discover_browser_root() -> str | None:
    """Return a usable Playwright browser directory, if one can be found.

    ``PLAYWRIGHT_BROWSERS_PATH`` wins. Otherwise check the conventional
    per-user locations; a custom ``HOME``/``USERPROFILE`` can hide a browser
    cache that is installed under the OS user's real profile.
    """
    import os

    configured = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if configured and Path(configured).is_dir():
        return configured

    candidates = []
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "ms-playwright")
    candidates.append(Path(os.path.expanduser("~")) / "AppData" / "Local" / "ms-playwright")
    candidates.append(Path(os.path.expanduser("~")) / ".cache" / "ms-playwright")
    # The real Windows profile may differ from the (possibly sandboxed) HOME.
    candidates.extend(
        path
        for path in Path("C:/Users").glob("*/AppData/Local/ms-playwright")
        if path.is_dir()
    )
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("chromium-*")):
            return str(candidate)
    return None


def playwright_available() -> bool:
    """Return True when a Chromium build can actually be launched."""
    import os

    try:
        import playwright  # noqa: F401
    except Exception:  # noqa: BLE001 - import-time environment issues
        return False

    require_browser = os.getenv("MLX_MDX_REQUIRE_BROWSER") == "1"
    if not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        discovered = _discover_browser_root()
        if discovered:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = discovered

    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return require_browser

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:  # noqa: BLE001 - missing download or OS limitation
        return require_browser


def write_page_html(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path
