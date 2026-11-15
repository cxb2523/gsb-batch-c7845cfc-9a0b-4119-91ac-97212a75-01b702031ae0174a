"""Regression tests for the original single-file behaviour.

The batch feature must not change what happens when the input entry
contains a plain file: same extraction, same layout options, same
all-or-nothing error reporting via ``show_error``.
"""

import re

import pytest

import extractors
from helpers import make_extractor, read_csv_rows, read_sheet_rows, write


def test_single_file_excel_columns(workspace):
    tmp_path, input_dir = workspace
    source = input_dir / 'one.txt'
    write(source, 'id 10 id 20')
    output = tmp_path / 'single.xlsx'

    extractor = make_extractor(excel=True, columns=True)
    extractor.prepare_to_extract_data(
        str(output), str(source), 'Sheet1', [r'id \d+']
    )

    flat = [cell for row in read_sheet_rows(str(output), 'Sheet1') for cell in row]
    assert flat == ['id 10', 'id 20']
    assert 'single.xlsx' in extractor.log_text.content


def test_single_file_csv(workspace):
    tmp_path, input_dir = workspace
    source = input_dir / 'one.txt'
    write(source, 'v1 v2')
    output = tmp_path / 'single.csv'

    extractor = make_extractor(excel=False, columns=False)
    extractor.prepare_to_extract_data(str(output), str(source), '', [r'v\d'])

    assert read_csv_rows(str(output)) == [['v1', 'v2']]


def test_single_file_binary_reports_error(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    source = input_dir / 'binary.txt'
    with open(source, 'wb') as f:
        f.write(b'\xff\xfe\x00\x80')

    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))

    extractor = make_extractor(excel=False)
    extractor.prepare_to_extract_data(
        str(tmp_path / 'o.csv'), str(source), '', [r'\w+']
    )

    assert errors and 'binary file' in errors[0].lower()


def test_single_file_invalid_regex_reports_error(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    source = input_dir / 'one.txt'
    write(source, 'abc')

    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(err))

    extractor = make_extractor(excel=False)
    extractor.prepare_to_extract_data(
        str(tmp_path / 'o.csv'), str(source), '', [r'(']
    )

    assert errors and isinstance(errors[0], extractors.PatternError)


def test_single_file_missing_input_reports_error(workspace, monkeypatch):
    tmp_path, _ = workspace
    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))

    extractor = make_extractor(excel=False)
    extractor.prepare_to_extract_data(
        str(tmp_path / 'o.csv'), str(tmp_path / 'nope.txt'), '', [r'x']
    )

    assert errors


def test_single_file_unsupported_excel_extension(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    source = input_dir / 'one.txt'
    write(source, 'abc')

    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))

    extractor = make_extractor(excel=True)
    extractor.prepare_to_extract_data(
        str(tmp_path / 'o.doc'), str(source), 'Data', [r'abc']
    )

    assert errors and 'not supported' in errors[0]


def test_extract_data_and_column_order_unchanged():
    data = DataExtractor = extractors.DataExtractor
    extracted = data.extract_data([r'\d', r'[a-z]'], '1 a 2 b')
    assert extracted == [['1', '2'], ['a', 'b']]

    arranged = data.create_column_order([['1', '2'], ['a', 'b']])
    assert list(arranged) == [('1', 'a'), ('2', 'b')]
