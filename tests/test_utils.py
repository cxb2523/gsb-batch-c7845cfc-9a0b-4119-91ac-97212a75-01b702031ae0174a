"""Tests for the directory scanning helper."""

import os

from utils import list_text_files


def test_list_text_files_filters_and_sorts(tmp_path):
    (tmp_path / 'b.txt').write_text('b', encoding='utf-8')
    (tmp_path / 'a.log').write_text('a', encoding='utf-8')
    (tmp_path / 'ignore.bin').write_bytes(b'\x00\x01')
    (tmp_path / 'no_extension').write_text('x', encoding='utf-8')
    os.mkdir(tmp_path / 'subdir')
    (tmp_path / 'subdir' / 'nested.txt').write_text('nested', encoding='utf-8')

    result = list_text_files(str(tmp_path))
    names = [os.path.basename(path) for path in result]

    # Sorted, text extensions only, non recursive.
    assert names == ['a.log', 'b.txt']


def test_list_text_files_empty_directory(tmp_path):
    assert list_text_files(str(tmp_path)) == []


def test_list_text_files_case_insensitive_extension(tmp_path):
    (tmp_path / 'UPPER.TXT').write_text('x', encoding='utf-8')
    result = list_text_files(str(tmp_path))
    assert len(result) == 1
