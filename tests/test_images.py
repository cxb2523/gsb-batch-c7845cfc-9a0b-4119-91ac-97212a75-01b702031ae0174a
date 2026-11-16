"""Unit tests for image downloading outcomes and Markdown link rewriting."""

from __future__ import annotations

import re
from pathlib import Path

import requests

from mlx_mdx.images import MAX_IMAGE_BYTES, MIN_IMAGE_BYTES, download_images
from mlx_mdx.markdown import replace_image_links
from mlx_mdx.models import ImageAsset, ImageCandidate, ImageOutcome, ImageOutcomeStatus

from .support import VALID_PNG

DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
    "C0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _candidate(url: str, alt: str = "", src: str | None = None) -> ImageCandidate:
    return ImageCandidate(original_src=src or url, absolute_url=url, alt_text=alt)


def _asset(filename: str = "image-01-a.png", src: str = "/a.png") -> ImageAsset:
    return ImageAsset(
        original_src=src,
        absolute_url=f"https://example.test{src}",
        alt_text=Path(filename).stem,
        filename=filename,
        relative_path=f"images/{filename}",
    )


def test_every_candidate_gets_exactly_one_outcome(image_server, tmp_path):
    ok_url = image_server.add("/ok.png", body=VALID_PNG, content_type="image/png")
    missing_url = image_server.url("/missing.png")
    small_url = image_server.add("/small.png", body=b"tiny", content_type="image/png")
    text_url = image_server.add(
        "/not-image.png",
        body=b"<html>not an image</html>" * 40,
        content_type="text/html",
    )
    large_url = image_server.add(
        "/large.png",
        body=b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_IMAGE_BYTES + 10),
        content_type="image/png",
    )

    candidates = [
        _candidate(ok_url, "ok"),
        _candidate(missing_url, "missing"),
        _candidate(small_url, "small"),
        _candidate(text_url, "text"),
        _candidate(large_url, "large"),
        _candidate(DATA_URI, "inline"),
        _candidate(ok_url, "duplicate"),
    ]
    result = download_images(candidates, tmp_path)

    assert len(result.outcomes) == len(candidates)
    assert [outcome.status for outcome in result.outcomes] == [
        ImageOutcomeStatus.SAVED,
        ImageOutcomeStatus.SKIPPED_REQUEST_FAILED,
        ImageOutcomeStatus.SKIPPED_TOO_SMALL,
        ImageOutcomeStatus.SKIPPED_UNSUPPORTED_TYPE,
        ImageOutcomeStatus.SKIPPED_TOO_LARGE,
        ImageOutcomeStatus.SKIPPED_DATA_URI,
        ImageOutcomeStatus.SAVED,
    ]

    # One file per distinct URL, and the duplicate points at it.
    assert len(result.assets) == 1
    saved = result.assets[0]
    assert (tmp_path / saved.relative_path).read_bytes() == VALID_PNG
    assert result.outcomes[6].relative_path == saved.relative_path

    # Every non-saved outcome carries a human-readable reason.
    for outcome in result.skipped_outcomes:
        assert outcome.detail


def test_timeout_is_recorded_and_does_not_abort_other_images(image_server, tmp_path):
    slow_url = image_server.add(
        "/slow.png", body=VALID_PNG, content_type="image/png", delay=1.5
    )
    ok_url = image_server.add("/ok.png", body=VALID_PNG, content_type="image/png")

    result = download_images(
        [_candidate(slow_url, "slow"), _candidate(ok_url, "ok")],
        tmp_path,
        request_timeout=0.2,
    )

    assert [o.status for o in result.outcomes] == [
        ImageOutcomeStatus.SKIPPED_REQUEST_FAILED,
        ImageOutcomeStatus.SAVED,
    ]
    assert "timed out" in result.outcomes[0].detail.lower()
    assert (tmp_path / result.assets[0].relative_path).is_file()


def test_http_error_status_is_recorded(image_server, tmp_path):
    error_url = image_server.add("/error.png", status=500, body=b"boom")
    result = download_images([_candidate(error_url)], tmp_path)
    assert result.outcomes[0].status is ImageOutcomeStatus.SKIPPED_REQUEST_FAILED
    assert "500" in result.outcomes[0].detail


def test_write_failure_is_recorded_and_cleans_up(image_server, tmp_path, monkeypatch):
    ok_url = image_server.add("/ok.png", body=VALID_PNG, content_type="image/png")

    def _fail_write(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", _fail_write)
    result = download_images([_candidate(ok_url)], tmp_path)

    assert result.outcomes[0].status is ImageOutcomeStatus.SKIPPED_WRITE_FAILED
    assert "disk full" in result.outcomes[0].detail
    assert list((tmp_path / "images").glob("*.png")) == []


def test_unexpected_exception_is_isloated_per_image(image_server, tmp_path, monkeypatch):
    first_url = image_server.add("/first.png", body=VALID_PNG, content_type="image/png")
    second_url = image_server.add("/second.png", body=VALID_PNG, content_type="image/png")

    original_get = requests.Session.get

    def _flaky_get(self, url, *args, **kwargs):
        if "first" in url:
            raise ValueError("synthetic get failure")
        return original_get(self, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "get", _flaky_get, raising=True)

    result = download_images(
        [_candidate(first_url, "first"), _candidate(second_url, "second")], tmp_path
    )

    assert [o.status for o in result.outcomes] == [
        ImageOutcomeStatus.SKIPPED_UNEXPECTED_ERROR,
        ImageOutcomeStatus.SAVED,
    ]
    assert "synthetic get failure" in result.outcomes[0].detail


def test_declared_content_length_skips_without_reading_body(image_server, tmp_path):
    url = image_server.add(
        "/big.png",
        body=b"\x89PNG\r\n\x1a\n" + b"0" * 2048,
        content_type="image/png",
        headers={"Content-Length": str(MAX_IMAGE_BYTES + 1)},
    )
    result = download_images([_candidate(url)], tmp_path)
    assert result.outcomes[0].status is ImageOutcomeStatus.SKIPPED_TOO_LARGE
    assert "Content-Length" in result.outcomes[0].detail


def test_saved_link_is_rewritten_and_failed_link_is_dropped(tmp_path):
    saved = _asset()
    (tmp_path / "images").mkdir()
    (tmp_path / saved.relative_path).write_bytes(VALID_PNG)

    body = (
        "Intro paragraph.\n\n"
        "![Good chart](https://example.test/a.png)\n\n"
        "![Missing figure](https://example.test/missing.png)\n\n"
        "![Also missing](images/ghost.png)\n"
    )
    updated = replace_image_links(
        body,
        [saved],
        outcomes=[
            ImageOutcome(
                original_src=saved.original_src,
                absolute_url=saved.absolute_url,
                alt_text=saved.alt_text,
                status=ImageOutcomeStatus.SAVED,
                relative_path=saved.relative_path,
            )
        ],
        output_dir=tmp_path,
    )

    assert "![Good chart](images/image-01-a.png)" in updated
    assert "https://example.test/a.png" not in updated
    assert "![Missing figure]" not in updated
    assert "Missing figure" in updated
    assert "ghost.png" not in updated
    assert "Also missing" in updated


def test_relative_and_absolute_local_paths_resolve_only_when_saved(tmp_path):
    saved = _asset(filename="image-01-diagram.png")
    (tmp_path / "images").mkdir()
    target = tmp_path / saved.relative_path
    target.write_bytes(VALID_PNG)

    body = (
        "![local relative](images/image-01-diagram.png)\n"
        f"![absolute]({target.as_posix()})\n"
        "![unknown relative](images/never-downloaded.png)\n"
    )
    updated = replace_image_links(body, [saved], output_dir=tmp_path)

    assert "![local relative](images/image-01-diagram.png)" in updated
    assert "![absolute](images/image-01-diagram.png)" in updated
    assert "never-downloaded.png" not in updated


def test_html_img_tags_are_rewritten_or_dropped(tmp_path):
    saved = _asset(filename="image-01-alt-a.png")
    (tmp_path / "images").mkdir()
    (tmp_path / saved.relative_path).write_bytes(VALID_PNG)

    body = (
        '<img src="https://example.test/a.png" alt="alt a" width="120">\n'
        '<img src="https://example.test/b.png" alt="alt b">\n'
    )
    updated = replace_image_links(body, [saved], output_dir=tmp_path)

    assert 'src="images/image-01-alt-a.png"' in updated
    assert 'width="120"' in updated
    assert "example.test/b.png" not in updated
    assert "alt b" in updated
    assert "<img" not in updated.splitlines()[1]


def test_no_reference_left_pointing_at_missing_files(tmp_path):
    saved = _asset()
    (tmp_path / "images").mkdir()
    (tmp_path / saved.relative_path).write_bytes(VALID_PNG)

    body = (
        "![a](https://example.test/a.png)\n"
        "![b](https://example.test/b.png)\n"
        "![c](images/c.png)\n"
        f"![d]({DATA_URI})\n"
    )
    updated = replace_image_links(body, [saved], output_dir=tmp_path)

    references = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", updated)
    assert len(references) == 1
    for reference in references:
        assert (tmp_path / reference).is_file(), reference


def test_result_is_list_compatible_for_legacy_callers(image_server, tmp_path):
    ok_url = image_server.add("/ok.png", body=VALID_PNG, content_type="image/png")
    result = download_images([_candidate(ok_url)], tmp_path)

    assets = list(result)
    assert len(assets) == 1
    assert assets[0] is result.assets[0]
    assert len(result) == 1
    assert result[0] is assets[0]


def test_limits_are_sane():
    assert 0 < MIN_IMAGE_BYTES < MAX_IMAGE_BYTES


def test_reference_style_images_and_definitions(tmp_path):
    saved = _asset()
    (tmp_path / "images").mkdir()
    (tmp_path / saved.relative_path).write_bytes(VALID_PNG)

    body = (
        "![saved ref][s1] and ![dropped ref][s2]\n\n"
        "[s1]: <https://example.test/a.png> \"with title\"\n"
        "[s2]: https://example.test/gone.png\n"
    )
    skipped = ImageOutcome(
        original_src="https://example.test/gone.png",
        absolute_url="https://example.test/gone.png",
        alt_text="gone",
        status=ImageOutcomeStatus.SKIPPED_REQUEST_FAILED,
        detail="404",
    )
    updated = replace_image_links(
        body, [saved], outcomes=[skipped], output_dir=tmp_path
    )

    assert "![saved ref](images/image-01-a.png)" in updated
    assert "[s1]: images/image-01-a.png \"with title\"" in updated
    assert "dropped ref" in updated
    assert "![dropped ref]" not in updated
    assert "[s2]:" not in updated


def test_ordinary_reference_links_survive_pruning(tmp_path):
    saved = _asset()
    (tmp_path / "images").mkdir()
    (tmp_path / saved.relative_path).write_bytes(VALID_PNG)

    body = (
        "See [the source][src] for details.\n\n"
        "[src]: https://example.test/article\n"
    )
    updated = replace_image_links(body, [saved], output_dir=tmp_path)

    assert "[the source][src]" in updated
    assert "[src]: https://example.test/article" in updated


def test_fenced_code_and_inline_code_are_not_rewritten(tmp_path):
    saved = _asset()
    (tmp_path / "images").mkdir()
    (tmp_path / saved.relative_path).write_bytes(VALID_PNG)

    body = (
        "![real](https://example.test/a.png)\n\n"
        "```markdown\n![example](https://example.test/gone.png)\n```\n\n"
        "Use `![example](https://example.test/gone.png)` in your docs.\n"
    )
    updated = replace_image_links(body, [saved], output_dir=tmp_path)

    assert "![real](images/image-01-a.png)" in updated
    assert "```markdown\n![example](https://example.test/gone.png)\n```" in updated
    assert "`![example](https://example.test/gone.png)`" in updated


def test_untitled_absolute_path_inside_output_dir_is_accepted(tmp_path):
    saved = _asset()
    (tmp_path / "images").mkdir()
    target = tmp_path / saved.relative_path
    target.write_bytes(VALID_PNG)

    updated = replace_image_links(f"![abs]({target.as_posix()})", [saved], output_dir=tmp_path)
    assert updated == "![abs](images/image-01-a.png)"


def test_absolute_path_outside_output_dir_is_dropped(tmp_path):
    saved = _asset()
    (tmp_path / "images").mkdir()
    (tmp_path / saved.relative_path).write_bytes(VALID_PNG)

    outside = Path(tmp_path).parent / "outside.png"
    updated = replace_image_links(f"![abs]({outside.as_posix()})", [saved], output_dir=tmp_path)
    assert updated == "abs"
