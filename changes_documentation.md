# Changes Documentation 

## NHANES pyTOOL API for Version 0.1.0 to 0.1.1

### Class: NHANESDataAPI

### Added Features:
- **Private Class Attributes:** Made `cycle_list` and `data_category_list` private class attributes by adding double underscores before their names. This ensures they are only accessible within the class.

### Modified Methods:
- **`list_file_names(data_category, cycle_years=None)` Method:** Updated the method to handle private class attributes by using the double underscores before `cycle_list` and `data_category_list` names within the class.

### New Methods:

- **`_check_cycle(input_cycle)` and `_check_in_between_cycle(start_year, end_year)` Private Methods:** Added private methods to check the validity of input cycle years and return valid cycles based on input. These methods handle single cycle years, lists of single cycle years, cycle ranges, and lists of cycle ranges as acceptable input formats.

- **`_get_data_filename(data_category, cycle_year, data_file_description)` Private Method:** Added a private method to get the data file name for a specific cycle year and data file description. It handles private class attributes by using the double underscores before `cycle_list` and `data_category_list` names within the class.

### Updated Documentation:
- **Class Description:** Added detailed class description including constructor information, available attributes, and methods with their descriptions.

- **Method Descriptions:** Updated method descriptions to provide clear explanations of the functionality, parameters, and return values of each method.

- **Exception Handling:** Documented exceptions raised in methods and provided explanations for possible errors.

- **Input Formats:** Explained acceptable input formats for methods that require cycle years input, making it clearer for users how to provide valid input.

- **GitHub Integration:** Provided instructions on how to create a GitHub repository, clone it, add a documentation file (`changes_documentation.md`), and push changes to GitHub.

These changes enhance the functionality of the `NHANESDataAPI` class, improve code organization, and provide comprehensive documentation for users. Users can now easily understand the class's purpose, available methods, and how to interact with the API effectively.

## NHANES pyTOOL API - Multi-cycle retrieve_data resilience

### Class: NHANESDataAPI

### Modified Methods:
- **`retrieve_data(data_category, cycle, filename, include_uncommon_variables=True)`:** The multi-cycle path no longer aborts (discarding already-retrieved cycles) when a single cycle fails, and no longer silently skips cycles with no data file. Each requested cycle is settled individually:
  - Cycles whose data file is missing are marked `skipped` (with a reason) instead of being silently dropped.
  - Cycles whose retrieval raises an error are marked `failed` (with the exception details) instead of aborting the whole request.
  - If at least one cycle succeeds, the merged data from the successful cycles is returned. If every cycle fails or is skipped, a `ValueError` is still raised (no empty-frame "success").
  - When `include_uncommon_variables=False`, the merged data is trimmed to the variables common to the cycles that actually succeeded.
  - The `year` column continues to mark the source cycle of every row.
  - The single-cycle path keeps its existing return/raise behavior.

### New Methods:
- **`_read_cycle_data(cycle_year, data_file_name)` Private Method:** Single point where raw cycle data is loaded (network access by default). Can be overridden/patched to inject locally fabricated data for testing.
- **`get_last_cycle_report()` Method:** Returns the per-cycle settlement report from the most recent multi-cycle `retrieve_data` call.

### New Attributes:
- **`last_cycle_report`:** A list of `{'cycle', 'status', 'reason'}` dicts (one per requested cycle, in request order; status is `success`, `skipped`, or `failed`). Also attached to multi-cycle results as `result.attrs['cycle_report']`, so the settlement info travels with the return value rather than only being printed.
