import pandas as pd

INVOICE_DATE_FORMAT = "%d-%m-%Y"


def clean_sales_data(df):
    """Shared cleaning for every data source (CSV / SQL / Google Sheets).

    Parses ``invoice_date`` and drops rows whose date cannot be parsed,
    so downstream filtering/plotting never sees invalid dates.

    Returns:
        (cleaned_df, dropped_rows): the cleaned DataFrame and the number
        of rows that were dropped because of an unparseable invoice_date.
    """
    df = df.copy()
    df["invoice_date"] = pd.to_datetime(
        df["invoice_date"], format=INVOICE_DATE_FORMAT, errors="coerce"
    )
    invalid_mask = df["invoice_date"].isna()
    dropped_rows = int(invalid_mask.sum())
    cleaned = df.loc[~invalid_mask].reset_index(drop=True)
    return cleaned, dropped_rows
