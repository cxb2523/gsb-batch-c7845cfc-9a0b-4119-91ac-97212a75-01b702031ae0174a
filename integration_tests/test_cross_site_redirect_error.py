import json
import subprocess

from flask import Blueprint, redirect

from . import util


def make_blueprint(dead_port: int) -> Blueprint:
    blueprint = Blueprint("main", __name__)

    @blueprint.route("/")
    def root():
        return """
            <a href="/leave">
            <a href="/ok">
        """

    @blueprint.route("/leave")
    def leave():
        # The starting site redirects to a foreign host which cannot be reached.
        return redirect(f"http://localhost:{dead_port}/outside")

    @blueprint.route("/ok")
    def ok():
        return """<a href="/">\n"""

    return blueprint


def test_json(http_server) -> None:
    # Nothing listens on this port, so the external HEAD probe fails to connect.
    dead_port = 59001
    http_server(blueprint=make_blueprint(dead_port=dead_port), port=5000)

    result = subprocess.run(
        util.command(url="http://localhost:5000", json=True),
        stdout=subprocess.PIPE,
    )

    # One broken link makes the run fail, but the crawl itself completes and still
    # inspects the other pages.
    assert result.returncode == 1
    data = json.loads(result.stdout.decode())

    root_links = data["http://localhost:5000"]["links"]
    failed = next(link for link in root_links if link["href"] == "/leave")
    healthy = next(link for link in root_links if link["href"] == "/ok")

    assert failed == {
        "href": "/leave",
        "url": "http://localhost:5000/leave",
        "results": [
            {
                "type": "redirect",
                "status_code": 302,
                "value": f"http://localhost:{dead_port}/outside",
                "url": f"http://localhost:{dead_port}/outside",
            },
            {
                "type": "request_error",
                "message": "All connection attempts failed",
            },
        ],
    }
    assert healthy == {
        "href": "/ok",
        "url": "http://localhost:5000/ok",
        "results": [
            {
                "type": "response",
                "status_code": 200,
            },
        ],
    }

    # The healthy page was still crawled, proving one URL's request error did not abort
    # the whole run, and no foreign host was ever treated as a crawled page.
    assert "http://localhost:5000/ok" in data
    assert f"http://localhost:{dead_port}/outside" not in data
