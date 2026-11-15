"""Batch (directory) extraction tests.

Covers the three required paths:
  1. a normal batch run over several text files,
  2. an unreadable/binary file being skipped while the rest still land,
  3. an illegal regex being skipped while the valid patterns still run,
plus empty-directory, no-match, single-workbook-open, exact-order,
row-mode and data-retention checks.
"""

import os

import openpyxl
import pytest

import extractors
from extractors import BatchResult, DataExtractor
from helpers import (FakeLog, make_extractor, read_csv_rows, read_sheet_rows,
                     write)


# --------------------------------------------------------------------------
# 1. Normal batch run
# --------------------------------------------------------------------------

def test_batch_excel_columns_appends_every_file_into_one_workbook(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'id 100 and id 101')
    write(input_dir / 'b.txt', 'id 200')

    output = tmp_path / 'out.xlsx'
    extractor = make_extractor(excel=True, columns=True)

    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'id \d+']
    )

    assert isinstance(result, BatchResult)
    assert result.success_count == 2
    assert result.failed_files == 0
    assert result.total_matches == 3

    rows = read_sheet_rows(str(output), 'Data')
    # One row per matched value, all in column A, coming from both files.
    flat = [cell for row in rows for cell in row]
    assert sorted(flat) == ['id 100', 'id 101', 'id 200']

    # The log accounts for each file and gives the final settlement.
    log = extractor.log_text.content
    assert 'a.txt' in log and 'b.txt' in log
    assert 'Successful files: 2' in log
    assert 'Failed files: 0' in log
    assert 'out.xlsx' in log


def test_batch_csv_writes_all_files_into_one_csv(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'x 1 x 2')
    write(input_dir / 'b.txt', 'x 3')

    output = tmp_path / 'out.csv'
    extractor = make_extractor(excel=False, columns=True)

    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), '', [r'\d']
    )

    assert result.success_count == 2
    assert result.total_matches == 3

    rows = read_csv_rows(str(output))
    flat = [cell for row in rows for cell in row]
    assert sorted(flat) == ['1', '2', '3']


def test_batch_only_reads_recognised_text_extensions(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'hello')
    # Unknown extension: must be ignored, not counted as a failure.
    write(input_dir / 'mystery.xyz', 'hello')
    os.mkdir(input_dir / 'subdir')

    output = tmp_path / 'out.csv'
    extractor = make_extractor(excel=False)

    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), '', [r'hello']
    )

    assert result.processed_files == 1
    assert result.success_count == 1
    assert result.file_results[0]['file'] == 'a.txt'


def test_batch_opens_and_saves_workbook_only_once(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'id 1')
    write(input_dir / 'b.txt', 'id 2')
    write(input_dir / 'c.txt', 'id 3')

    output = tmp_path / 'out.xlsx'
    extractor = make_extractor(excel=True)

    load_calls = []
    real_load = openpyxl.load_workbook

    def tracking_load(path, *args, **kwargs):
        load_calls.append(path)
        return real_load(path, *args, **kwargs)

    monkeypatch.setattr(extractors.openpyxl, 'load_workbook', tracking_load)
    monkeypatch.setattr(
        extractors.ExcelFileExtractor, 'close_output',
        staticmethod(lambda wb, output_file: (wb.save(output_file), wb.close()))
    )

    save_count = 0
    real_close = extractors.ExcelFileExtractor.close_output

    def counting_close(wb, output_file):
        nonlocal save_count
        save_count += 1
        return real_close(wb, output_file)

    monkeypatch.setattr(
        extractors.ExcelFileExtractor, 'close_output', staticmethod(counting_close)
    )

    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'id \d']
    )

    assert result.success_count == 3
    assert len(load_calls) == 1
    assert save_count == 1


# --------------------------------------------------------------------------
# 2. Bad / unreadable files are skipped without losing written data
# --------------------------------------------------------------------------

def test_batch_skips_binary_file_and_keeps_other_files_data(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    good = input_dir / 'good.txt'
    bad = input_dir / 'bad.txt'

    write(good, 'id 100')
    # Bytes that cannot be decoded as UTF-8.
    with open(bad, 'wb') as f:
        f.write(b'\xff\xfe\x00id \x80\x81')

    output = tmp_path / 'out.xlsx'
    info_messages = []
    monkeypatch.setattr(extractors, 'show_info', lambda msg: info_messages.append(msg))

    extractor = make_extractor(excel=True)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'id \d+']
    )

    assert result.processed_files == 2
    assert result.success_count == 1
    assert result.failed_files == 1

    failed = [item for item in result.file_results if not item['success']]
    assert failed and failed[0]['file'] == 'bad.txt'
    assert 'binary' in failed[0]['reason'].lower() or 'encoding' in failed[0]['reason'].lower()

    # The good file's data must still be in the workbook -- no rollback.
    flat = [cell for row in read_sheet_rows(str(output), 'Data') for cell in row]
    assert flat == ['id 100']

    log = extractor.log_text.content
    assert '[skipped file] bad.txt' in log
    assert 'Successful files: 1' in log
    assert 'Failed files: 1' in log


def test_batch_skips_unreadable_file_due_to_permissions(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    write(input_dir / 'good.txt', 'id 7')
    locked = input_dir / 'locked.txt'
    write(locked, 'id 9')

    import builtins
    real_open = builtins.open

    def deny_open(path, *args, **kwargs):
        if os.path.abspath(str(path)) == os.path.abspath(str(locked)):
            raise PermissionError('Access is denied')
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', deny_open)

    output = tmp_path / 'out.csv'
    extractor = make_extractor(excel=False)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), '', [r'id \d']
    )

    assert result.failed_files == 1
    assert result.success_count == 1
    failed = [item for item in result.file_results if not item['success']]
    assert failed[0]['file'] == 'locked.txt'
    assert 'denied' in failed[0]['reason'].lower() or 'permission' in failed[0]['reason'].lower()

    assert read_csv_rows(str(output)) == [['id 7']]


def test_batch_skips_bad_file_between_good_ones(workspace):
    tmp_path, input_dir = workspace
    # Sorted order: bad.txt sits between a.txt and c.txt.
    write(input_dir / 'a.txt', 'id 1')
    with open(input_dir / 'bad.txt', 'wb') as f:
        f.write(b'\xff\xfe\x00\x80')
    write(input_dir / 'c.txt', 'id 3')

    output = tmp_path / 'out.csv'
    extractor = make_extractor(excel=False)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), '', [r'id \d']
    )

    assert result.processed_files == 3
    assert result.success_count == 2
    assert result.failed_files == 1
    assert read_csv_rows(str(output)) == [['id 1'], ['id 3']]


# --------------------------------------------------------------------------
# 3. Illegal regexes are skipped without aborting the batch
# --------------------------------------------------------------------------

def test_batch_skips_invalid_regex_and_runs_valid_ones(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'cat 1 bat 2')

    output = tmp_path / 'out.xlsx'
    extractor = make_extractor(excel=True, columns=True)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data',
        [r'cat \d', r'(unclosed', r'bat \d']
    )

    assert len(result.skipped_patterns) == 1
    skipped_pattern, reason = result.skipped_patterns[0]
    assert skipped_pattern == r'(unclosed'
    assert 'invalid regex' in reason

    # Both valid patterns still extracted, data from both is present.
    flat = [cell for row in read_sheet_rows(str(output), 'Data') for cell in row]
    assert 'cat 1' in flat and 'bat 2' in flat

    log = extractor.log_text.content
    assert '[skipped pattern]' in log
    assert '(unclosed' in log


def test_batch_invalid_regex_does_not_affect_other_files(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'v 1')
    write(input_dir / 'b.txt', 'v 2')

    output = tmp_path / 'out.csv'
    extractor = make_extractor(excel=False)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), '', [r'[', r'v \d']
    )

    assert result.success_count == 2
    assert result.total_matches == 2
    assert read_csv_rows(str(output)) == [['v 1'], ['v 2']]


def test_batch_all_patterns_invalid_raises_error_and_writes_nothing(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'data')
    output = tmp_path / 'out.xlsx'

    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))

    extractor = make_extractor(excel=True)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'(', r'[']
    )

    assert errors and 'None of the patterns are valid' in errors[0]
    assert result.processed_files == 0
    assert not output.exists()


def test_validate_patterns_rejects_multi_group_pattern():
    valid, skipped = DataExtractor.validate_patterns([r'(\w)-(\d)', r'id (\d+)'])
    assert valid == [r'id (\d+)']
    assert len(skipped) == 1
    assert 'multiple groups' in skipped[0][1]


# --------------------------------------------------------------------------
# Empty directory / no matches / validation
# --------------------------------------------------------------------------

def test_batch_empty_directory_shows_clear_error(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    output = tmp_path / 'out.xlsx'

    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))

    extractor = make_extractor(excel=True)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'\w+']
    )

    assert errors and 'No text files were found' in errors[0]
    assert not output.exists()


def test_batch_no_matches_informs_user_but_still_succeeds(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'nothing useful')
    output = tmp_path / 'out.xlsx'

    info_messages = []
    monkeypatch.setattr(extractors, 'show_info', lambda msg: info_messages.append(msg))

    extractor = make_extractor(excel=True)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'\d{10}']
    )

    assert result.success_count == 1
    assert result.failed_files == 0
    assert result.total_matches == 0
    assert info_messages and 'none of the patterns matched' in info_messages[0].lower()
    assert output.exists()
    assert 'Total values written: 0' in extractor.log_text.content


def test_batch_missing_patterns_or_output_reports_error(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'x')

    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))
    extractor = make_extractor(excel=True)

    extractor.prepare_to_extract_data_batch('', str(input_dir), 'Data', [r'x'])
    extractor.prepare_to_extract_data_batch(str(tmp_path / 'o.xlsx'), str(input_dir), 'Data', [])

    assert any('output file is required' in msg.lower() for msg in errors)
    assert any('no patterns' in msg.lower() for msg in errors)


def test_batch_nonexistent_directory_reports_error(workspace, monkeypatch):
    tmp_path, _ = workspace
    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))

    extractor = make_extractor(excel=False)
    result = extractor.prepare_to_extract_data_batch(
        str(tmp_path / 'o.csv'), str(tmp_path / 'missing'), '', [r'x']
    )

    assert errors and 'is not a directory' in errors[0]
    assert result.processed_files == 0


def test_batch_unsupported_output_extension_reports_error(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'x')
    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))

    extractor = make_extractor(excel=True)
    result = extractor.prepare_to_extract_data_batch(
        str(tmp_path / 'out.doc'), str(input_dir), 'Data', [r'x']
    )

    assert errors and 'not supported' in errors[0]
    assert result.processed_files == 0


def test_batch_never_uses_output_file_as_input(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'id 1')
    # Output lives inside the input directory: it must be excluded.
    output = input_dir / 'out.csv'

    extractor = make_extractor(excel=False)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), '', [r'id \d']
    )

    assert result.processed_files == 1
    assert result.file_results[0]['file'] == 'a.txt'
    assert read_csv_rows(str(output)) == [['id 1']]


def test_batch_when_only_candidate_is_the_output_file_reports_error(workspace, monkeypatch):
    tmp_path, input_dir = workspace
    # The only text file in the directory is the output file itself.
    output = input_dir / 'out.csv'
    write(output, 'preexisting')

    errors = []
    monkeypatch.setattr(extractors, 'show_error', lambda err: errors.append(str(err)))

    extractor = make_extractor(excel=False)
    result = extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), '', [r'id \d']
    )

    assert errors and 'No usable text files were found' in errors[0]
    assert result.processed_files == 0
    # The pre-existing output content is untouched.
    assert read_csv_rows(str(output)) == [['preexisting']]


# --------------------------------------------------------------------------
# Existing option semantics must keep working in batch mode
# --------------------------------------------------------------------------

def test_batch_excel_row_mode_writes_one_row_per_pattern(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'a1 a2 b1')

    output = tmp_path / 'out.xlsx'
    extractor = make_extractor(excel=True, columns=False)  # put in rows

    extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'a\d', r'b\d']
    )

    rows = read_sheet_rows(str(output), 'Data')
    # Row mode: each pattern is one row; its matches run across columns.
    assert rows[0] == ['a1', 'a2']
    assert rows[1] == ['b1']


def test_batch_excel_exact_order_writes_per_pattern_columns(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'x1 x2 y1')
    write(input_dir / 'b.txt', 'x3')

    output = tmp_path / 'out.xlsx'
    extractor = make_extractor(excel=True, columns=True, exact=True)

    extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'x\d', r'y\d']
    )

    rows = read_sheet_rows(str(output), 'Data')
    column_a = [row[0] for row in rows if row]
    column_b = [row[1] for row in rows if len(row) > 1]
    assert column_a == ['x1', 'x2', 'x3']
    assert column_b == ['y1']


def test_batch_appends_to_existing_output(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'id 1')

    output = tmp_path / 'out.xlsx'
    first = make_extractor(excel=True)
    first.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'id \d']
    )

    write(input_dir / 'b.txt', 'id 2')
    second = make_extractor(excel=True)
    second.prepare_to_extract_data_batch(
        str(output), str(input_dir), 'Data', [r'id \d']
    )

    flat = [cell for row in read_sheet_rows(str(output), 'Data') for cell in row]
    assert sorted(flat) == ['id 1', 'id 1', 'id 2']


def test_batch_csv_row_mode_layout(workspace):
    tmp_path, input_dir = workspace
    write(input_dir / 'a.txt', 'a1 b1')

    output = tmp_path / 'out.csv'
    extractor = make_extractor(excel=False, columns=False)  # put in rows
    extractor.prepare_to_extract_data_batch(
        str(output), str(input_dir), '', [r'a\d', r'b\d']
    )

    rows = read_csv_rows(str(output))
    assert rows == [['a1'], ['b1']]
