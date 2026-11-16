import json
import subprocess

from flask import Blueprint, abort, redirect, request

from . import util


def make_blueprint() -> Blueprint:
    blueprint = Blueprint("main", __name__)

    @blueprint.route("/")
    def root():
        # The starting page itself redirects to a different host.
        return redirect("http://localhost:5001/outside")

    return blueprint


def make_external_blueprint() -> tuple[Blueprint, list[str]]:
    blueprint = Blueprint("external", __name__)
    hits: list[str] = []

    @blueprint.route("/outside", methods=["GET", "HEAD"])
    def outside():
        hits.append(request.method)
        # Content which would expose new links if the body were wrongly parsed.
        return """<a href="http://localhost:5002/never-crawled">\n"""

    return blueprint, hits


def make_target_blueprint() -> tuple[Blueprint, list[str]]:
    blueprint = Blueprint("target", __name__)
    hits: list[str] = []

    @blueprint.route("/never-crawled", methods=["GET", "HEAD"])
    def never_crawled():
        hits.append(request.method)
        return ""

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

    # The foreign host answers the HEAD probe, so there is nothing broken.
    assert result.returncode == 0
    # No page of the foreign host (nor its outgoing links) is ever crawled, hence the
    # analysis has no page to report.
    assert json.loads(result.stdout.decode()) == {}

    # Exactly one HEAD probe was performed and the foreign body was never fetched.
    assert external_hits == ["HEAD"]
    assert target_hits == []


def make_external_no_head_blueprint() -> tuple[Blueprint, list[str]]:
    blueprint = Blueprint("external_no_head", __name__)
    hits: list[str] = []

    @blueprint.route("/outside", methods=["HEAD"])
    def outside_head():
        hits.append("HEAD")
        abort(405)

    @blueprint.route("/outside", methods=["GET"])
    def outside_get():
        hits.append("GET")
        return ""

    return blueprint, hits


def test_head_405_falls_back_to_get(http_server) -> None:
    http_server(blueprint=make_blueprint(), port=5000)
    external_blueprint, external_hits = make_external_no_head_blueprint()
    http_server(blueprint=external_blueprint, port=5001)

    result = subprocess.run(
        util.command(url="http://localhost:5000", json=True),
        stdout=subprocess.PIPE,
    )

    # HEAD answered 405, so a single GET was issued as a fallback; both settle the same
    # URL once, without crawling its content.
    assert result.returncode == 0
    assert json.loads(result.stdout.decode()) == {}
    assert external_hits == ["HEAD", "GET"]
