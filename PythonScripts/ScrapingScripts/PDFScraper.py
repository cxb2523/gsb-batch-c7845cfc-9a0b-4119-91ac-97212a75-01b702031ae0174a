import fitz
import os
import pandas as pd

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Callable, Any, Optional

from datetime import datetime
from dateutil.relativedelta import relativedelta

# _________________________Custom Modules_________________________
import PythonScripts.ScrapingScripts.FileManagement as FileManagement

from PythonScripts.ScrapingScripts.PDFTextAnalyst import PDFTextAnalyst


# Dataframe columns
equity_columns = ["Symbol", "Name", "Quantity", "Price"]
equity_numeric = ["Quantity", "Price"]

option_columns = ["Symbol", "Name", "Quantity", "Price", "Market Value"]
option_numeric = ["Quantity", "Price", "Market Value"]

fixed_income_columns = ["CUSIP", "Name", "Par", "Market Price", "Market Value"]
fi_numeric = ["Par", "Market Price", "Market Value"]


# _________________________Read from PDF_________________________
def _read_pdf(pdf_name: str) -> Dict[int, str]:
    """
    Extracts and splits text content from each page_number of a PDF document.

    This method reads a PDF file, extracts the text content from each page_number, and splits the text into lines.

    :param pdf_name: The name of the PDF file.
    :return: A dictionary where the keys represent page_number numbers and the values represent the extracted and
             split text content from each page_number.
    :raises FileNotFoundError: If the specified PDF file is not found in the folder.
    """

    pdf_path = os.path.join(FileManagement.statement_directory_path, pdf_name)

    # Check if the PDF file exists
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"File {pdf_name} was not found in the folder.")

    # Initialize an empty dictionary to store extracted and split text content
    extracted_pages = {}

    # Open the PDF document using fitz
    with fitz.Document(pdf_path) as file:
        # Iterate through each page_number in the PDF document
        for page_number in file:
            # Extract text content from the current page_number and split it into lines
            text_lines = page_number.get_textpage().extractText()
            extracted_pages[page_number.number + 1] = text_lines

    return extracted_pages


# Replaceable read seam: swap_statement resolves the reader through this module global at
# call time, so tests (or alternative storage backends) can substitute a stub without
# touching fitz or the statement directory.
pdf_text_reader: Callable[[str], Dict[int, str]] = _read_pdf


class MonthlyReadStatus(str, Enum):
    """
    Outcome of processing a single monthly statement.

    SUCCESS: the statement was read and the extraction function returned a value.
    MISSING: no statement file exists for the month (FileNotFoundError while reading).
    PARSE_ERROR: the statement could not be read or the extraction function failed.
    """

    SUCCESS = "success"
    MISSING = "missing"
    PARSE_ERROR = "parse_error"


@dataclass
class MonthlyPeriodResult:
    """
    Per-period outcome of a monthly read-back.

    :ivar month: The month label used as the table index, formatted as "%Y-%B".
    :ivar file_name: The statement file name that was attempted ("%Y-%B.pdf").
    :ivar status: Outcome of the attempt (see MonthlyReadStatus).
    :ivar error_message: String form of the exception for MISSING/PARSE_ERROR, else None.
    """

    month: str
    file_name: str
    status: MonthlyReadStatus
    error_message: Optional[str] = None

    @property
    def successful(self) -> bool:
        return self.status == MonthlyReadStatus.SUCCESS


@dataclass
class MonthlyReadResult:
    """
    Result of a fault-tolerant monthly read-back.

    ``data`` only contains columns/rows for successful months; ``period_results`` keeps the
    outcome of *every* attempted month (including missing and failed ones), in iteration
    order. The two collections are intentionally separate so a failed period can never
    shift the positional alignment of the successful values.

    :ivar data: DataFrame indexed by the successful month labels.
    :ivar period_results: One MonthlyPeriodResult per attempted month.
    :ivar restoration_error: If swapping back to the originally opened statement failed,
                             its string form; otherwise None.
    """

    data: pd.DataFrame
    period_results: List[MonthlyPeriodResult] = field(default_factory=list)
    restoration_error: Optional[str] = None

    @property
    def successful_periods(self) -> List[MonthlyPeriodResult]:
        return [result for result in self.period_results if result.status == MonthlyReadStatus.SUCCESS]

    @property
    def missing_periods(self) -> List[MonthlyPeriodResult]:
        return [result for result in self.period_results if result.status == MonthlyReadStatus.MISSING]

    @property
    def parse_error_periods(self) -> List[MonthlyPeriodResult]:
        return [result for result in self.period_results if result.status == MonthlyReadStatus.PARSE_ERROR]

    @property
    def failed_periods(self) -> List[MonthlyPeriodResult]:
        return [
            result for result in self.period_results
            if result.status != MonthlyReadStatus.SUCCESS
        ]

    @property
    def all_periods_failed(self) -> bool:
        return bool(self.period_results) and not self.successful_periods


@dataclass
class PDFScraper(PDFTextAnalyst):
    """
    A class for extracting and processing investment data from PDF documents.

    This class provides methods to extract investment data from PDF documents,
    process the data, and convert it into pandas DataFrames for further analysis.
    It supports extracting data related to different asset types, such as stocks,
    fixed income, exchange-traded funds (ETFs), and more.

    """

    _currently_opened_statement: str = field(init=False)

    # _________________________Properties_________________________
    @property
    def symbols_of_fixed_income_etfs(self) -> List[str]:
        return FileManagement.extract_fixed_income_etf_tickers()

    @property
    def currently_opened_statement(self) -> str:
        """
        Get the currently opened Schwab statement file.

        This property performance the file name of the currently opened Schwab statement. It allows you to
        identify which statement is currently being processed or analyzed.

        :return: The file name of the currently opened Schwab statement.
        """
        return self._currently_opened_statement

    @property
    def pdf(self):
        """
        Get the dictionary of extracted PDF content.

        :return: A dictionary where keys represent page numbers and values represent extracted text content.
        """
        return self._pdf_file

    # _________________________Asset Class Getters_________________________
    @property
    def scraped_options(self) -> pd.DataFrame:
        """
        Retrieve and convert options data from the PDF statement into a DataFrame.

        This property extracts options data from the PDF statement, converts it into a DataFrame, and performance it.
        The resulting DataFrame contains information about options, including columns like symbol,
        quantity, market value, etc.

        :return: A DataFrame containing options data.
        """
        return self._convert_generator_of_asset_data_to_dataframe(
            "Options", option_columns, option_numeric
        )

    @property
    def scraped_stocks(self) -> pd.DataFrame:
        """
        Get the DataFrame containing equity investment information.

        :return: A DataFrame containing equity investment data.
        """
        return self._convert_generator_of_asset_data_to_dataframe("Equities", equity_columns,
                                                                  equity_numeric)

    @property
    def scraped_bond_funds(self) -> pd.DataFrame:
        """
        Get the DataFrame containing bond fund investment information.

        :return: A DataFrame containing equity investment data.
        """
        return self._convert_generator_of_asset_data_to_dataframe("Bond Funds", equity_columns, equity_numeric)

    @property
    def scraped_equity_funds(self) -> pd.DataFrame:
        """
        Get the DataFrame containing equity fund investment information.

        :return: A DataFrame containing equity investment data.
        """

        return self._convert_generator_of_asset_data_to_dataframe("Equity Funds", equity_columns, equity_numeric)

    @property
    def scraped_exchange_traded_funds(self) -> pd.DataFrame:
        """
        Get the DataFrame containing Exchange Traded Fund (ETF) investment information.

        :return: A DataFrame containing ETF investment data.
        """
        return self._convert_generator_of_asset_data_to_dataframe(
            "Exchange Traded Funds", equity_columns, equity_numeric)

    @property
    def scraped_other_assets(self) -> pd.DataFrame:
        """
        Get a DataFrame containing information about other assets (e.g., ETFs) from the statements.

        :return: A pandas DataFrame containing information about other assets.
        """
        return self._convert_generator_of_asset_data_to_dataframe("Other Assets", equity_columns, equity_numeric)

    @property
    def scraped_money_market_funds(self) -> pd.DataFrame:
        """
        Property to retrieve a DataFrame containing investment details for Money Market Funds.

        :return: A pandas DataFrame containing investment details for Money Market Funds.
        """
        return self._convert_generator_of_asset_data_to_dataframe("Fund Name", equity_columns, equity_numeric)

    @property
    def scraped_corporate_bonds(self) -> pd.DataFrame:
        """
        Property to retrieve a DataFrame containing investment details for Corporate Bonds.

        :return: A pandas DataFrame containing investment details for Corporate Bonds.
        """
        return self._convert_generator_of_asset_data_to_dataframe(
            "Corporate Bonds", fixed_income_columns, fi_numeric
        )

    @property
    def scraped_bond_partial_calls(self) -> pd.DataFrame:
        """
        Retrieve a DataFrame containing information about bond partial calls.

        This property method extracts and processes data related to bond partial calls
        from the PDFScraper instance. It converts the extracted data into a pandas DataFrame
        and performance it.

        :return: A pandas DataFrame containing information about bond partial calls.
        :rtype: pd.DataFrame
        """
        return self._convert_generator_of_asset_data_to_dataframe(
            "Other Fixed Income", fixed_income_columns, fi_numeric
        )

    @property
    def scraped_treasuries(self) -> pd.DataFrame:
        """
        Property to retrieve a DataFrame containing investment details for U.S. Treasuries.

        :return: A pandas DataFrame containing investment details for U.S. Treasuries.
        """
        return self._convert_generator_of_asset_data_to_dataframe(
            "U.S. Treasuries", fixed_income_columns, fi_numeric
        )

    # _________________________Additional Data Getters_________________________
    @property
    def scraped_cash_transactions(self) -> float:
        try:
            return self.scraped_cash_transaction_summary["This Period"].loc["Total Cash Transaction Detail"]
        except KeyError:
            return self.scraped_cash_transaction_summary["This Period"].loc["Total Transaction Detail"]

    @property
    def scraped_cash_transaction_summary(self):
        """
        Get a summary DataFrame of cash transactions for the portfolio.

        This property retrieves and performance a summary DataFrame of cash transactions for the portfolio, which can
        provide insights into the cash flow activity within the portfolio.

        :return: A DataFrame summarizing cash transactions.
        """
        return self._cash_transaction_summary()

    @property
    def change_in_account_value(self) -> pd.DataFrame:
        """
        Get a DataFrame showing changes in the account's value.

        This property retrieves and performance a DataFrame that shows the changes in the account's value over time.
        It can be useful for tracking the performance and fluctuations of the portfolio.

        :return: A DataFrame with changes in account value.
        """
        return self._change_in_account_value()

    @property
    def account_value(self):
        """
        Get the total value of the account for the current period.

        :return: Total account value for the current period.
        """
        return self.change_in_account_value["This Period"][-1]

    @property
    def year_to_date_numerical_value(self) -> float:
        return datetime.strptime(self.currently_opened_statement.split(".")[0], "%Y-%B").month

    @property
    def asset_composition(self) -> pd.DataFrame:
        """
        Get the DataFrame containing asset assets information.

        :return: A DataFrame containing asset assets data.
        """
        return self._provided_asset_composition()

    # _________________________Decorators_________________________
    def standard_iterator(self, func: Callable[..., Any]) -> pd.DataFrame:
        # Convert the input string to a datetime object
        start_date = datetime.strptime(
            self._currently_opened_statement.split(".")[0],
            "%Y-%B"
        )

        months_to_subtract = [3, start_date.month, 12, 12 * 3, 12 * 5]
        periods = ["3 Month", "Year to Date", "1 Year", "3 Year", "5 Year"]

        schwab_statements = {}
        for months, period in zip(months_to_subtract, periods):
            statement_date = (start_date - relativedelta(months=months)).strftime("%Y-%B")
            statement_path = f"{statement_date}.pdf"

            schwab_statements[statement_path] = period

        def wrapper():
            dataframe = pd.DataFrame()
            for path, statement_period in schwab_statements.items():
                self.swap_statement(path)
                dataframe[statement_period] = func()

            return dataframe

        self.revert_to_original_pdf_file()
        return wrapper()

    def monthly_iterator(self, func: Callable[..., Any], add_additional_month: bool) -> MonthlyReadResult:
        """
        Iterate through the last five years of monthly statements, one month per column.

        A single missing or unparseable statement no longer aborts the whole read-back:
        the month is recorded in the result's ``period_results`` and skipped, while all
        remaining months are still processed. The returned frame only contains successful
        months, each labelled with the month the values were extracted from, so columns can
        never be shifted by a skipped month.

        :param func: Zero-argument extraction function evaluated against the swapped
                     statement (e.g. a closure reading ``self.account_value``).
        :param add_additional_month: When True, include one extra month beyond five years.
        :return: A MonthlyReadResult holding the month-indexed DataFrame and the per-period
                 outcomes (success / missing / parse error). If every month fails,
                 ``data`` is an empty DataFrame and ``period_results`` still covers all
                 attempted months. No exception is raised for per-period failures.
        """

        statements = self._monthly_statement_paths(add_additional_month)

        dataframe = pd.DataFrame()

        def extract_month() -> Any:
            return func()

        def store_success(month: str, extracted_value: Any) -> None:
            # Assigning by explicit month label keeps each value bound to its own month;
            # skipped months simply never create a column.
            dataframe[month] = extracted_value

        return self._iterate_monthly_statements(
            statements=statements,
            extraction_func=extract_month,
            store_success=store_success,
            build_data=lambda: dataframe,
        )

    def monthly_iterator_multi_column_frame(
            self, func: Callable[..., Any], add_additional_month: bool, columns: List[str]) -> MonthlyReadResult:
        """
        Iterate through the last five years of monthly statements, one row per month.

        Behaves like :meth:`monthly_iterator` but ``func`` is expected to return a row of
        values aligned with ``columns``. Successful rows are stored together with their
        month label and only assembled into the frame at the end, so a skipped month can
        never shift a row onto the wrong month index.

        :param func: Zero-argument extraction function returning one row for ``columns``.
        :param add_additional_month: When True, include one extra month beyond five years.
        :param columns: Column names for the resulting DataFrame.
        :return: A MonthlyReadResult. When every month fails, ``data`` is an empty
                 DataFrame with ``columns`` and no rows rather than an exception.
        """

        statements = self._monthly_statement_paths(add_additional_month)

        successful_rows: List[Any] = []
        successful_months: List[str] = []

        def store_success(month: str, extracted_value: Any) -> None:
            successful_months.append(month)
            successful_rows.append(extracted_value)

        def build_data() -> pd.DataFrame:
            return pd.DataFrame(successful_rows, columns=columns, index=successful_months)

        return self._iterate_monthly_statements(
            statements=statements,
            extraction_func=func,
            store_success=store_success,
            build_data=build_data,
        )

    # _________________________Monthly Iteration Helpers_________________________
    def _monthly_statement_paths(self, add_additional_month: bool) -> Dict[str, str]:
        """
        Build the ordered mapping of statement file name to month label for the monthly
        read-back, starting at the currently opened statement and walking backwards.
        """

        # Convert the input string to a datetime object
        start_date = datetime.strptime(
            self._currently_opened_statement.split(".")[0],
            "%Y-%B"
        )

        additional_months = 2 if add_additional_month else 1
        schwab_statements: Dict[str, str] = {}

        for months in range((12 * 5) + additional_months):
            statement_date = (start_date - relativedelta(months=months)).strftime("%Y-%B")
            statement_path = f"{statement_date}.pdf"

            schwab_statements[statement_path] = statement_date

        return schwab_statements

    def _iterate_monthly_statements(
            self,
            statements: Dict[str, str],
            extraction_func: Callable[[], Any],
            store_success: Callable[[str, Any], None],
            build_data: Callable[[], pd.DataFrame]) -> MonthlyReadResult:
        """
        Shared fault-tolerant loop backing both monthly iterators.

        Each period is isolated: a missing file or any exception raised while reading the
        statement or running ``extraction_func`` is captured as a MonthlyPeriodResult and
        the loop continues with the next month. At the end the scraper always swaps back to
        the statement that was opened when the iteration started, even when every period
        failed or a restoration error occurs.
        """

        original_statement = self._currently_opened_statement
        period_results: List[MonthlyPeriodResult] = []

        for path, month in statements.items():
            try:
                self.swap_statement(path)
                extracted_value = extraction_func()
            except FileNotFoundError as error:
                period_results.append(
                    MonthlyPeriodResult(
                        month=month,
                        file_name=path,
                        status=MonthlyReadStatus.MISSING,
                        error_message=str(error),
                    )
                )
                continue
            except Exception as error:  # noqa: BLE001 - any parse/extraction failure must be isolated
                period_results.append(
                    MonthlyPeriodResult(
                        month=month,
                        file_name=path,
                        status=MonthlyReadStatus.PARSE_ERROR,
                        error_message=f"{type(error).__name__}: {error}",
                    )
                )
                continue

            period_results.append(
                MonthlyPeriodResult(
                    month=month,
                    file_name=path,
                    status=MonthlyReadStatus.SUCCESS,
                )
            )
            store_success(month, extracted_value)

        restoration_error: Optional[str] = None
        try:
            self.swap_statement(original_statement)
        except Exception as error:  # noqa: BLE001 - surface restoration failure without masking results
            restoration_error = f"{type(error).__name__}: {error}"

        return MonthlyReadResult(
            data=build_data(),
            period_results=period_results,
            restoration_error=restoration_error,
        )

    # _________________________Class Methods_________________________
    def revert_to_original_pdf_file(self):
        self.swap_statement(FileManagement.extract_schwab_statements())

    def swap_statement(self, new_file_name: str) -> None:
        """
        Swap the PDF content with a new PDF file.

        This method replaces the current PDF content with the content of a new PDF file.
        It reads and extracts the text content from each page of the new PDF and updates the internal PDF dictionary.
        The actual reading is delegated to the module-level ``pdf_text_reader`` seam so it
        can be replaced with a stub for testing or alternative storage.

        :param new_file_name: The name of the new PDF file.
        :raises FileNotFoundError: If the new statement file does not exist.
        """
        # Read and extract text content from each page of the new PDF
        new_pdf_content = pdf_text_reader(new_file_name)

        # Update the internal PDF dictionary with the new PDF content
        self._pdf_file = new_pdf_content
        self._currently_opened_statement = new_file_name

    def __post_init__(self):
        self._currently_opened_statement = FileManagement.extract_schwab_statements()


# FileManagement.validate_statement_files(statement_folder_path=statements_directory_path)

pdf_file_data: Dict[int, str] = _read_pdf(FileManagement.extract_schwab_statements())

pdf_scraper = PDFScraper(
    _pdf_file=pdf_file_data,
)
