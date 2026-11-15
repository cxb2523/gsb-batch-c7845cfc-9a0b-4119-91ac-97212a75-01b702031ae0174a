from .data_source_csv import load_data_from_csv
from .data_source_sql import load_data_from_sql
from .data_source_gsheet import load_data_from_gsheet
from .data_cleaning import clean_sales_data
from config.settings import DATA_SOURCE as DEFAULT_SOURCE, GSHEET_SHEET_NAME, GSHEET_WORKSHEET_NAME


def load_data(source_type=None, **kwargs):
    """Load the raw sales DataFrame from the configured source (no cleaning)."""
    source = source_type or DEFAULT_SOURCE

    if source == "csv":
        return load_data_from_csv(kwargs.get("filepath", "data/sales_data.csv"))

    elif source in ("sqlite", "mysql", "postgres"):
        return load_data_from_sql(source)

    elif source == "gsheet":
        return load_data_from_gsheet(
            sheet_name=kwargs.get("sheet_name") or GSHEET_SHEET_NAME,
            worksheet_name=kwargs.get(
                "worksheet_name") or GSHEET_WORKSHEET_NAME
        )

    else:
        raise ValueError(f"Unsupported data source: {source}")


def load_clean_data(source_type=None, **kwargs):
    """Load data from any source and apply the shared cleaning logic.

    Every source (CSV, SQLite, MySQL, PostgreSQL, Google Sheets) goes
    through the same clean_sales_data() step.

    Returns:
        (cleaned_df, dropped_rows)
    """
    df = load_data(source_type=source_type, **kwargs)
    return clean_sales_data(df)
