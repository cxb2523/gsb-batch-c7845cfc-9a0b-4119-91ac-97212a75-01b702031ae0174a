import json
import subprocess

from flask import Blueprint, redirect, request

from . import util


def make_blueprint() -> Blueprint:
    blueprint = Blueprint("main", __name__)

    @blueprint.route("/")
    def root():
        return """<a href="/leave">\n"""

    @blueprint.route("/leave")
    def leave():
        # A page of the starting site redirects the browser to another site.
        return redirect("http://localhost:5001/outside")

    return blueprint


def make_external_blueprint() -> tuple[Blueprint, list[str]]:
    blueprint = Blueprint("external", __name__)
    hits: list[str] = []

    @blueprint.route("/outside", methods=["GET", "HEAD"])
    def outside():
        # Record how the foreign host is contacted: the crawl must only HEAD-probe it,
        # never GET its body, and the redirect it returns must not be followed.
        hits.append(request.method)
        return redirect("http://localhost:5002/never-crawled")

    return blueprint, hits


def make_target_blueprint() -> tuple[Blueprint, list[str]]:
    blueprint = Blueprint("target", __name__)
    hits: list[str] = []

    @blueprint.route("/never-crawled", methods=["GET", "HEAD"])
    def never_crawled():
        hits.append(request.method)
        return """<a href="http://localhost:5000/">\n"""

    return blueprint, hits


def test_json(http_server) -> None:
    http_server(blueprint=make_blueprint(), port=5000)
    external_blueprint, external_hits = make_external_blueprint()
    target_blueprint, target_hits = make_target_blueprint()
    http_server(blueprint=external_blueprint, port=5001)
    http_server(blueprint=target_blueprint, port=5002)

    result = subprocess.run(
        util.command(url="http://localhost:5000", json=True),
        stdout=subprocess.PIPE,
    )

    # The redirect target answers the HEAD probe, so the link is not broken.
    assert result.returncode == 0
    assert json.loads(result.stdout.decode()) == {
        "http://localhost:5000": {
            "links": [
                {
                    "href": "/leave",
                    "url": "http://localhost:5000/leave",
                    "results": [
                        {
                            "type": "redirect",
                            "status_code": 302,
                            "value": "http://localhost:5001/outside",
                            "url": "http://localhost:5001/outside",
                        },
                        {
                            "type": "redirect",
                            "status_code": 302,
                            "value": "http://localhost:5002/never-crawled",
                            "url": "http://localhost:5002/never-crawled",
                        },
                    ],
                },
            ],
        },
    }

    # The foreign host was contacted by exactly one HEAD request and never GET-fetched.
    assert external_hits == ["HEAD"]
    # The second redirect target belongs to yet another host and was never requested.
    assert target_hits == []


def test_text(http_server) -> None:
    http_server(blueprint=make_blueprint(), port=5000)
    external_blueprint, _ = make_external_blueprint()
    target_blueprint, _ = make_target_blueprint()
    http_server(blueprint=external_blueprint, port=5001)
    http_server(blueprint=target_blueprint, port=5002)

    result = subprocess.run(
        util.command(url="http://localhost:5000"),
        stdout=subprocess.PIPE,
    )

    # Everything is reachable, so only the summary line is printed. The line ending is
    # normalized because Windows translates `\n` to `\r\n` when writing to the console.
    assert result.returncode == 0
    assert result.stdout.decode().replace("\r\n", "\n") == util.output_str(
        "📂 Results: 1 links (1 ok, 0 failed)"
    )
