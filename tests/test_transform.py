# pandas builds the small tables the tests feed into the functions.
import pandas as pd

# pytest checks that a piece of code stops with the expected error.
import pytest

# The forecast function under test.
from src.forecasting import forecast_weekly_sales
# The cleaning and summary functions under test.
from src.transform import (
    clean_dataset,
    detect_dataset_type,
    holiday_uplift,
    monthly_sales,
    normalize_weekly_columns,
    store_performance,
)


# pytest runs every function whose name starts with test_.
# This one guards the date format: 05-02-2010 has to mean 5 February, not 2 May.
def test_normalize_weekly_columns_parses_day_first_dates():
    # One raw row with the same column names and date style as the real CSV.
    raw = pd.DataFrame(
        # Each key is a column name. Each list holds that column's values, one per row.
        {
            # Store number.
            "Store": [1],
            # Day first, then month, then year.
            "Date": ["05-02-2010"],
            # Sales for the week.
            "Weekly_Sales": [100.0],
            # 0 means a normal week.
            "Holiday_Flag": [0],
            # The last four columns only need to exist. This test never reads their values.
            "Temperature": [42.31],
            # Copied from the first row of the real file.
            "Fuel_Price": [2.572],
            # Also from the real file.
            "CPI": [211.096358],
            # Also from the real file.
            "Unemployment": [8.106],
        }
    )

    # Run the cleaning.
    df = normalize_weekly_columns(raw)

    # assert stops the test with an error if the condition is false.
    # Row 0 must have the date 2010-02-05.
    assert df.loc[0, "date"] == pd.Timestamp("2010-02-05")
    # Cleaning must not change the sales number.
    assert df.loc[0, "weekly_sales"] == 100.0


# The store with the most sales must come out on top.
def test_store_performance_ranks_by_total_sales():
    # Three rows: store 1 sells 100. Store 2 sells 150 and 200.
    raw = pd.DataFrame(
        # Column name, then one value per row.
        {
            # Row 1 belongs to store 1, rows 2 and 3 to store 2.
            "Store": [1, 2, 2],
            # 5 Feb, 5 Feb and 12 Feb 2010.
            "Date": ["05-02-2010", "05-02-2010", "12-02-2010"],
            # Store 1 has 100 in total. Store 2 has 150 + 200 = 350.
            "Weekly_Sales": [100.0, 150.0, 200.0],
            # Only the last row is a holiday week.
            "Holiday_Flag": [0, 0, 1],
            # Filler value 1 for the four columns this test does not look at.
            "Temperature": [1, 1, 1],
            # Filler.
            "Fuel_Price": [1, 1, 1],
            # Filler.
            "CPI": [1, 1, 1],
            # Filler.
            "Unemployment": [1, 1, 1],
        }
    )
    # Clean the rows first, as the app does.
    df = normalize_weekly_columns(raw)

    # Build the ranking.
    ranking = store_performance(df)

    # iloc[0] is the first row. It must be store 2.
    assert ranking.iloc[0]["store"] == 2
    # Its total must be 150 + 200 = 350.
    assert ranking.iloc[0]["total_sales"] == 350.0


# Checks the monthly total and the holiday percentage with numbers that are easy to do by hand.
def test_monthly_sales_and_holiday_uplift():
    # Two weeks in February 2010: a normal week with 100 and a holiday week with 150.
    raw = pd.DataFrame(
        # Column name, then one value per row.
        {
            # Both rows are store 1.
            "Store": [1, 1],
            # 5 Feb and 12 Feb 2010, so both fall in the same month.
            "Date": ["05-02-2010", "12-02-2010"],
            # 100 in the normal week, 150 in the holiday week.
            "Weekly_Sales": [100.0, 150.0],
            # First row normal, second row holiday.
            "Holiday_Flag": [0, 1],
            # Filler value 1 for the four columns this test does not look at.
            "Temperature": [1, 1],
            # Filler.
            "Fuel_Price": [1, 1],
            # Filler.
            "CPI": [1, 1],
            # Filler.
            "Unemployment": [1, 1],
        }
    )
    # Clean the rows.
    df = normalize_weekly_columns(raw)

    # Both weeks are in the same month, so there is one row and it must say 100 + 150 = 250.
    assert monthly_sales(df).loc[0, "weekly_sales"] == 250.0
    # (150 - 100) / 100 * 100 = 50 percent.
    assert holiday_uplift(df) == 50.0


# The forecast must add exactly the number of weeks that was asked for.
def test_forecast_appends_requested_periods():
    # Eight Fridays in a row with slowly rising sales.
    raw = pd.DataFrame(
        # Column name, then one value per row.
        {
            # [1] * 8 is a list with eight 1s, so every row is store 1.
            "Store": [1] * 8,
            # Eight weekly dates starting Friday 5 February 2010.
            "Date": pd.date_range("2010-02-05", periods=8, freq="W-FRI"),
            # Sales climb from 100 to 130 with small dips on the way.
            "Weekly_Sales": [100, 110, 105, 115, 120, 118, 125, 130],
            # No holiday weeks.
            "Holiday_Flag": [0] * 8,
            # Filler value 1 for the four columns this test does not look at.
            "Temperature": [1] * 8,
            # Filler.
            "Fuel_Price": [1] * 8,
            # Filler.
            "CPI": [1] * 8,
            # Filler.
            "Unemployment": [1] * 8,
        }
    )
    # Clean the rows.
    df = normalize_weekly_columns(raw)

    # Ask for 4 future weeks.
    forecast = forecast_weekly_sales(df, periods=4)

    # 8 past rows + 4 future rows = 12.
    assert len(forecast) == 12
    # The last 4 rows must each have a forecast number. notna() is True where a value exists.
    assert forecast["forecast_sales"].tail(4).notna().all()


# A weekly file whose headers use a different case, stray spaces, or a hyphen instead of an
# underscore must still be recognised as weekly and cleaned with the right row count and numbers.
def test_weekly_name_variants_are_detected_and_cleaned():
    # Every header is a variant of the real one: " STORE ", "WEEKLY SALES", "holiday-flag".
    raw = pd.DataFrame(
        {
            # Extra spaces around the name.
            " STORE ": [1, 2],
            # Lowercase.
            "date": ["05-02-2010", "05-02-2010"],
            # Uppercase with a space instead of the underscore.
            "WEEKLY SALES": [100.0, 200.5],
            # Lowercase with a hyphen.
            "holiday-flag": [0, 1],
            # Ordinary spelling, to show the old names still work next to the new ones.
            "Temperature": [42.31, 38.51],
            # Uppercase, the underscore kept.
            "FUEL_PRICE": [2.572, 2.548],
            # Lowercase with stray spaces.
            " cpi ": [211.0963582, 211.2421698],
            # Uppercase with a trailing space.
            "Unemployment ": [8.106, 8.106],
        }
    )

    # The check must pick weekly, not POS.
    assert detect_dataset_type(raw) == "weekly"
    # The shared entry point must return the same label plus a clean table.
    df, dataset_type = clean_dataset(raw)
    # The label and the cleaned rows.
    assert dataset_type == "weekly"
    # No row may be lost: both rows have a store, a date and sales.
    assert len(df) == 2
    # The numbers must survive the renaming untouched, in date-and-store order.
    assert df["store"].tolist() == [1, 2]
    assert df["weekly_sales"].tolist() == [100.0, 200.5]
    # The holiday flag became a whole number and kept its values.
    assert df["holiday_flag"].tolist() == [0, 1]
    # The date was read day first, as the CSV writes it.
    assert df.loc[0, "date"] == pd.Timestamp("2010-02-05")
    # The renamed columns use the clean names the charts and SQL tables expect.
    for name in ["store", "date", "weekly_sales", "holiday_flag", "temperature", "fuel_price", "cpi", "unemployment"]:
        assert name in df.columns


# A file that really lacks a weekly column must still stop with the missing names.
def test_weekly_missing_column_still_raises_a_clear_error():
    # The same columns as the real file, except Weekly_Sales is absent.
    raw = pd.DataFrame(
        {
            "Store": [1],
            "Date": ["05-02-2010"],
            "Holiday_Flag": [0],
            "Temperature": [42.31],
            "Fuel_Price": [2.572],
            "CPI": [211.0963582],
            "Unemployment": [8.106],
        }
    )

    # The weekly cleaner must stop and name weekly_sales, in the same message as before.
    with pytest.raises(ValueError) as caught:
        normalize_weekly_columns(raw)
    # The message must list the column that is missing.
    assert str(caught.value) == "Missing weekly sales columns: ['weekly_sales']"
    # The file is still recognised as weekly: it has store and several weekly-only columns.
    assert detect_dataset_type(raw) == "weekly"
    # So the shared entry point reports the weekly column, not a list of POS columns.
    with pytest.raises(ValueError) as caught:
        clean_dataset(raw)
    assert "Missing weekly sales columns" in str(caught.value)
    assert "weekly_sales" in str(caught.value)


# A POS file with differently spelled headers must clean just as well as one with the original names.
def test_pos_name_variants_are_cleaned():
    raw = pd.DataFrame(
        {
            # Spaced and uppercase variants of the six required columns.
            "INVOICE ID": ["750-67-8428"],
            " branch ": ["A"],
            "PRODUCT-LINE": ["Health and beauty"],
            # The optional money columns, spelled differently too.
            "Unit-Price": [74.69],
            "quantity": [7],
            "TAX 5%": [26.1415],
            "total": [548.9715],
            # Straight from the sample file.
            "Date": ["2019-01-05"],
            " payment ": ["Ewallet"],
            # The remaining optional columns.
            "COGS": [522.83],
            "Gross Margin Percentage": [4.761904762],
            "gross-income": [26.1415],
            "Rating": [9.1],
        }
    )

    # The shared entry point must pick POS for this table.
    df, dataset_type = clean_dataset(raw)
    assert dataset_type == "pos"
    # One row in, one row out.
    assert len(df) == 1
    # The renamed columns are the names the POS page reads.
    for name in ["invoice_id", "branch", "product_line", "unit_price", "quantity", "tax_5_pct", "total", "date", "payment", "cogs", "gross_margin_percentage", "gross_income", "rating"]:
        assert name in df.columns
    # The numbers must keep their exact values.
    assert df.loc[0, "total"] == 548.9715
    assert df.loc[0, "unit_price"] == 74.69
    assert df.loc[0, "tax_5_pct"] == 26.1415
    # The date was read as the POS file writes it.
    assert df.loc[0, "date"] == pd.Timestamp("2019-01-05")
