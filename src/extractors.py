import openpyxl, re, os, csv
from dataclasses import dataclass, field
from openpyxl.utils import get_column_letter
from utils import *

from typing import TypeAlias
from openpyxl.worksheet.worksheet import Worksheet

# ``re.PatternError`` was named ``re.error`` before Python 3.11 and some
# stripped interpreter builds only expose the legacy name.
PatternError = getattr(re, 'PatternError', re.error)

ExtractedData : TypeAlias = tuple[tuple[str, ...], ...]
Patterns : TypeAlias = list[str]

@dataclass
class BatchResult:
    """Accounting record of a batch (directory) extraction run."""

    processed_files : int = 0
    failed_files : int = 0
    skipped_patterns : list = field(default_factory=list)        # list[(pattern, reason)]
    file_results : list = field(default_factory=list)            # list[dict]
    total_matches : int = 0

    @property
    def success_count(self) -> int:
        return self.processed_files - self.failed_files


class CSVFileExtractor:
    def __init__(self, col_var):
        self.col_var = col_var

    @staticmethod
    def arrange_rows(extracted_data, col_var_value):
        """Turn extracted per-pattern lists into the rows that get written.

        Mirrors the single-file CSV behaviour: column mode transposes the
        pattern lists into rows, row mode writes one list per pattern.
        """
        if col_var_value:
            return DataExtractor.create_column_order(extracted_data)
        return extracted_data

    def export_extracted_data_to_csv(self, output_file : str, patterns : Patterns ,content : str) -> str | None:
        extracted_data = DataExtractor.extract_data(patterns,content)

        if isinstance(extracted_data[0][0], tuple):
            raise TypeError

        if WithLogging.with_logging:
                extracted_data_copy = extracted_data

        rows = self.arrange_rows(extracted_data, self.col_var.get())

        with open(output_file,'a',newline='',encoding=ENCODING) as f:
            writer = csv.writer(f)
            writer.writerows(rows)

        if WithLogging.with_logging:
            return DataExtractor.get_extracted_data_string(extracted_data_copy)

    # ----- batch (directory) primitives: the output file is opened once -----

    def batch_write(self, csv_file, patterns : Patterns, content : str) -> int:
        """Write one input file's matches into the already open *csv_file*.

        Returns the number of matched values that were written.
        """
        extracted_data = DataExtractor.extract_data(patterns, content)
        match_count = sum(len(data_list) for data_list in extracted_data)

        rows = self.arrange_rows(extracted_data, self.col_var.get())

        writer = csv.writer(csv_file)
        writer.writerows(rows)

        return match_count


class ExcelFileExtractor:
    def __init__(self, col_var, exact_var):
        self.col_var = col_var
        self.exact_var = exact_var

    @staticmethod
    def find_max(index : int, sheet : Worksheet) -> int:
        row = 0
        for i in sheet.iter_rows(min_col=index,max_col=index):
            if i[0].value is not None:
                row = i[0].row
        return row

    @staticmethod
    def put_data_in_excel_without_exact_order(extracted_data : ExtractedData, sheet : Worksheet) -> None:
        for data_list in extracted_data:
            sheet.append(data_list)

    @staticmethod
    def get_cell(pattern_letter : str, row_number : int):
        return pattern_letter + str(row_number)

    @staticmethod
    def put_data_in_excel_with_exact_order(extracted_data : ExtractedData, sheet : Worksheet) -> None:
        column_letters_list = [get_column_letter(i) for i in range(1,len(extracted_data)+1)]

        find_max_index = 1
        columns_list_index = 0

        for data_list in extracted_data:
            row_number = ExcelFileExtractor.find_max(find_max_index,sheet) + 1
            for item in data_list:
                sheet[ExcelFileExtractor.get_cell(column_letters_list[columns_list_index],row_number)] = item
                row_number += 1
            columns_list_index += 1
            find_max_index += 1

    # ----- batch (directory) primitives: the workbook is loaded and saved once -----

    @staticmethod
    def open_output(output_file : str, sheet_name : str):
        """Load (or create) the workbook once and resolve the target sheet."""
        if not os.path.isfile(output_file):
            wb = openpyxl.Workbook()
            wb.save(output_file)
            wb.close()

        sheet_name = sheet_name.title()

        wb = openpyxl.load_workbook(output_file)

        if sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
        else:
            sheet = wb.create_sheet(sheet_name)

        return wb, sheet

    def batch_write(self, sheet : Worksheet, patterns : Patterns, content : str) -> int:
        """Write one input file's matches into the shared *sheet*.

        Returns the number of matched values that were written. The workbook
        itself is saved once, after every input file has been processed.
        """
        extracted_data = DataExtractor.extract_data(patterns, content)
        match_count = sum(len(data_list) for data_list in extracted_data)

        if self.col_var.get() and not self.exact_var.get():
            extracted_data = DataExtractor.create_column_order(extracted_data)

        if not self.exact_var.get(): # The codes in this if statement will not be executed if 'put in rows' is enabled
            ExcelFileExtractor.put_data_in_excel_without_exact_order(extracted_data, sheet)
        else:
            ExcelFileExtractor.put_data_in_excel_with_exact_order(extracted_data, sheet)

        return match_count

    @staticmethod
    def close_output(wb, output_file : str) -> None:
        wb.save(output_file)
        wb.close()

    def export_extracted_data_to_excel(self, output_file : str, sheet_name : str, patterns : Patterns, content : str) -> str | None:
                wb, sheet = self.open_output(output_file, sheet_name)

                extracted_data = DataExtractor.extract_data(patterns,content)

                if WithLogging.with_logging:
                    extracted_data_copy = extracted_data

                if self.col_var.get() and not self.exact_var.get():
                    extracted_data = DataExtractor.create_column_order(extracted_data)

                if not self.exact_var.get(): # The codes in this if statement will not be executed if 'put in rows' is enabled
                    ExcelFileExtractor.put_data_in_excel_without_exact_order(extracted_data,sheet)
                else:
                    ExcelFileExtractor.put_data_in_excel_with_exact_order(extracted_data,sheet)

                wb.save(output_file)
                wb.close()

                if WithLogging.with_logging:
                    return DataExtractor.get_extracted_data_string(extracted_data_copy)

class DataExtractor:
    def __init__(self, excel_var, log_text, col_var, exact_var):
        self.log_text = log_text
        self.excel_var = excel_var

        self.excel_extractor = ExcelFileExtractor(col_var, exact_var)
        self.csv_extractor = CSVFileExtractor(col_var)

    @staticmethod
    def extract_data(patterns : Patterns, content : str) -> ExtractedData:
        extracted_data = []
        for pattern in patterns:
            data_list = re.findall(pattern,content)
            extracted_data.append(data_list)

        return extracted_data

    @staticmethod
    def validate_patterns(patterns : Patterns) -> tuple[Patterns, list]:
        """Split *patterns* into valid ones and skipped ones.

        Every pattern is compiled once, up front: an illegal pattern is
        reported and skipped before any file is processed, so it never
        aborts the whole batch. Patterns with more than one capturing
        group are rejected for the same reason they fail in single-file
        mode (``re.findall`` would return tuples).
        """
        valid_patterns : Patterns = []
        skipped : list = []

        for pattern in patterns:
            try:
                compiled = re.compile(pattern)
            except PatternError as err:
                skipped.append((pattern, f'invalid regex: {err.msg}'))
                continue

            if compiled.groups > 1:
                skipped.append((pattern, 'a pattern cannot contain multiple groups'))
                continue

            valid_patterns.append(pattern)

        return valid_patterns, skipped

    def prepare_to_extract_data(self, output_file : str, input_file : str, sheet_name : str, patterns : Patterns) -> None:
            try:
                log_string = ''

                assert patterns, 'There is no patterns to extract data'
                assert output_file, 'The name of output file is required.'

                # --- read the content of input file ---

                with open(input_file,encoding=ENCODING) as f:
                    try:
                        content = f.read()
                    except UnicodeDecodeError:
                        raise ValueError('The input file cannot be a binary file')

                # --- deciding what to do with the output file ---

                output_file_extention = os.path.splitext(output_file)[1].lower()

                if self.excel_var.get():
                    if output_file_extention in ['.xlsx', '.xlsm', '.xltx', '.xltm']:
                        log_string = self.excel_extractor.export_extracted_data_to_excel(output_file,sheet_name,patterns,content)
                    else:
                        raise ValueError('The output file format is not supported. It should be .xlsx, .xlsm, .xltx or .xltm')

                else:
                    log_string = self.csv_extractor.export_extracted_data_to_csv(output_file,patterns,content)

                # --- handling the log ----

                if WithLogging.with_logging:
                    self.log_found_data(log_string, output_file)

            except (FileNotFoundError, AssertionError, PermissionError, ValueError, PatternError) as err:
                show_error(err)

            except TypeError:
                show_error("You cannot place multiple groups in a pattern")

    def prepare_to_extract_data_batch(self, output_file : str, input_directory : str,
                                      sheet_name : str, patterns : Patterns) -> BatchResult:
        """Extract matches from every text file in *input_directory*.

        Failures are isolated: an unreadable file or an illegal pattern is
        skipped with a log entry while the remaining files and patterns keep
        running. Already written data is never rolled back -- the workbook
        is saved once (CSV opened once) and only valid matches are written.

        Returns a :class:`BatchResult` with the per-file accounting; the same
        accounting is rendered into the log widget.
        """
        result = BatchResult()

        try:
            assert patterns, 'There is no patterns to extract data'
            assert output_file, 'The name of output file is required.'

            # --- validate every pattern once, up front ---

            valid_patterns, result.skipped_patterns = self.validate_patterns(patterns)

            if not valid_patterns:
                raise ValueError('None of the patterns are valid, so no data can be extracted.')

            # --- decide what to do with the output file ---

            output_file_extention = os.path.splitext(output_file)[1].lower()

            if self.excel_var.get():
                if output_file_extention not in ['.xlsx', '.xlsm', '.xltx', '.xltm']:
                    raise ValueError('The output file format is not supported. It should be .xlsx, .xlsm, .xltx or .xltm')
            elif output_file_extention != '.csv':
                raise ValueError('In CSV mode the output file extension should be .csv')

            # --- gather the input files ---

            if not os.path.isdir(input_directory):
                raise FileNotFoundError(f'{input_directory!r} is not a directory.')

            input_files = list_text_files(input_directory)

            if not input_files:
                raise ValueError(f'No text files were found in {input_directory!r}.')

            # Never treat the output file itself as an input file.
            input_files = [path for path in input_files
                           if os.path.abspath(path) != os.path.abspath(output_file)]

            if not input_files:
                raise ValueError(f'No usable text files were found in {input_directory!r}.')

            # --- open the output exactly once ---

            if self.excel_var.get():
                wb, sheet = self.excel_extractor.open_output(output_file, sheet_name)
                csv_file = None
            else:
                csv_file = open(output_file, 'a', newline='', encoding=ENCODING)
                wb = None
                sheet = None

            try:
                for input_path in input_files:
                    result.processed_files += 1
                    display_name = os.path.basename(input_path)

                    try:
                        with open(input_path, encoding=ENCODING) as f:
                            try:
                                content = f.read()
                            except UnicodeDecodeError:
                                raise ValueError('The input file cannot be read: binary or unsupported encoding')

                        if self.excel_var.get():
                            match_count = self.excel_extractor.batch_write(sheet, valid_patterns, content)
                        else:
                            match_count = self.csv_extractor.batch_write(csv_file, valid_patterns, content)

                    except (OSError, ValueError, PatternError) as err:
                        # Isolated failure: this file is skipped, everything
                        # already written stays in the output.
                        result.failed_files += 1
                        result.file_results.append({
                            'file': display_name, 'success': False,
                            'matches': 0, 'reason': str(err)
                        })
                        continue

                    result.total_matches += match_count
                    result.file_results.append({
                        'file': display_name, 'success': True,
                        'matches': match_count, 'reason': ''
                    })
            finally:
                # Single save/close, regardless of what happened per file.
                if self.excel_var.get():
                    self.excel_extractor.close_output(wb, output_file)
                else:
                    csv_file.close()

        except (FileNotFoundError, AssertionError, PermissionError, ValueError, PatternError) as err:
            show_error(err)
            return result

        except TypeError:
            show_error("You cannot place multiple groups in a pattern")
            return result

        # --- log the per-file accounting and the final settlement ---

        self.log_batch_result(result, input_directory, output_file)

        if result.total_matches == 0:
            show_info(f'All {result.success_count} readable file(s) were processed, '
                      'but none of the patterns matched anything.')

        return result

    def log_batch_result(self, result : BatchResult, input_directory : str, output_file : str) -> None:
        lines = [f'Batch extraction from {input_directory!r}:', '']

        for pattern, reason in result.skipped_patterns:
            lines.append(f'[skipped pattern] {pattern!r}: {reason}')

        if result.skipped_patterns:
            lines.append('')

        for item in result.file_results:
            if item['success']:
                lines.append(f"[ok] {item['file']}: {item['matches']} value(s) written")
            else:
                lines.append(f"[skipped file] {item['file']}: {item['reason']}")

        lines.append('')
        lines.append(f"Successful files: {result.success_count}")
        lines.append(f"Failed files: {result.failed_files}")
        lines.append(f"Total values written: {result.total_matches}")
        lines.append(f'{output_file!r} saved.')

        self.log_text.config(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.insert('end', '\n'.join(lines))
        self.log_text.config(state='disabled')
        self.log_text.see('end')

    @staticmethod
    def get_extracted_data_string(extracted_data_copy : ExtractedData) -> str:
        log_string = ''

        for data_list in extracted_data_copy:
            log_string += '\n'.join(data_list) + '\n'

        return log_string

    def log_found_data(self, log_string : str, output_file : str):
        log_string += f'\n{output_file!r} saved.' + '\n'
        self.log_text.config(state='normal')
        self.log_text.delete('1.0','end')
        self.log_text.insert('end', log_string)
        self.log_text.config(state='disabled')
        self.log_text.see('end')

    @staticmethod
    def create_column_order(extracted_data : ExtractedData) -> tuple[tuple[str]]:
        max_len = max([len(data_list) for data_list in extracted_data])

        for data_list in extracted_data:
            for _ in range(max_len - len(data_list)):
                data_list.append('')

        return tuple(zip(*extracted_data))
