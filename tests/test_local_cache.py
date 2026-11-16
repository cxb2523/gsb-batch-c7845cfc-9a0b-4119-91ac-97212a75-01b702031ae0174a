"""LocalCache durability and robustness."""

import json

from airbyte_connector_generator_poc.local_cache import LocalCache


def test_set_and_get_roundtrip(tmp_path):
    cache = LocalCache(str(tmp_path / "selector_cache.json"))
    cache.set("abc", {"main_section_selector": "#content"})

    assert cache.get("abc") == {"main_section_selector": "#content"}
    assert cache.get("missing") is None


def test_existing_entries_are_preserved(tmp_path):
    path = tmp_path / "selector_cache.json"
    path.write_text(json.dumps({"old": {"main_section_selector": "#a"}}),
                    encoding="utf-8")

    cache = LocalCache(str(path))
    cache.set("new", {"main_section_selector": "#b"})

    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"old", "new"}


def test_corrupted_cache_file_does_not_crash(tmp_path):
    path = tmp_path / "selector_cache.json"
    path.write_text("{ this is not json", encoding="utf-8")

    cache = LocalCache(str(path))
    assert cache.get("anything") is None
    cache.set("fresh", {"main_section_selector": "#c"})

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"fresh": {"main_section_selector": "#c"}}


def test_non_json_path_is_rejected(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        LocalCache(str(tmp_path / "cache.txt"))


def test_write_leaves_no_temp_files_behind(tmp_path):
    import os

    cache = LocalCache(str(tmp_path / "selector_cache.json"))
    cache.set("abc", {"main_section_selector": "#content"})

    leftovers = [name for name in os.listdir(tmp_path) if name.endswith(".tmp")]
    assert leftovers == []
