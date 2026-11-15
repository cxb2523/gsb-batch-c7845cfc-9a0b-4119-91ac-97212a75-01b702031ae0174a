"""Utility-level tests, notably URL-list reading hygiene."""

from amazon_product_details_scraper.core.utils import (
    ensure_folder,
    extract_image_extension,
    read_file,
)


def test_read_file_skips_blank_lines_and_trims_whitespace(tmp_path):
    list_file = tmp_path / "urls.txt"
    list_file.write_text(
        "\n"
        "  https://www.amazon.com/dp/B000ONE  \n"
        "\t\n"
        "https://www.amazon.in/dp/B000TWO\n"
        "   \n"
        "",
        encoding="utf8",
    )

    assert read_file(str(list_file)) == [
        "https://www.amazon.com/dp/B000ONE",
        "https://www.amazon.in/dp/B000TWO",
    ]


def test_read_file_empty_file_returns_empty_list(tmp_path):
    list_file = tmp_path / "empty.txt"
    list_file.write_text("", encoding="utf8")

    assert read_file(str(list_file)) == []


def test_extract_image_extension_ignores_query_string():
    assert extract_image_extension("https://host/a.JPG?x=1&y=2") == "jpg"
    assert extract_image_extension("https://host/no-extension") == ""


def test_ensure_folder_is_idempotent_and_keeps_contents(tmp_path):
    target = tmp_path / "product" / "images"
    ensure_folder(str(target))
    marker = target / "image_1.jpg"
    marker.write_bytes(b"keep-me")

    # A second call must not wipe the existing content
    ensure_folder(str(target))
    assert marker.read_bytes() == b"keep-me"
