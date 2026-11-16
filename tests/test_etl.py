# pandas writes the small CSV files the tests load.
import pandas as pd

# sqlalchemy reads the rows back out of the database.
from sqlalchemy import create_engine, text

# The loader under test.
from src.etl import load_weekly


# A helper for the tests below: writes a weekly CSV and returns its path.
# The headers use several spellings, because the loader has to accept those too.
def write_weekly_csv(path, stores, dates, sales) -> None:
    # One row per store, date and sales value.
    pd.DataFrame(
        # The same column names as the real file, with different case and spacing.
        {
            " STORE ": stores,
            "Date": dates,
            "WEEKLY SALES": sales,
            "holiday-flag": [0] * len(stores),
            "Temperature": [42.31] * len(stores),
            "Fuel_Price": [2.572] * len(stores),
            "CPI": [211.096] * len(stores),
            "Unemployment": [8.106] * len(stores),
        }
    # index=False leaves out the pandas row numbers.
    ).to_csv(path, index=False)


# Rows that already sit in the table must be skipped, and the other rows must still be written.
def test_load_weekly_skips_duplicate_store_dates(tmp_path, capsys):
    # A fresh SQLite database file. The loader creates the table itself.
    db_url = f"sqlite:///{tmp_path / 'weekly.db'}"
    # The first import: two weeks of store 1.
    first = tmp_path / "first.csv"
    write_weekly_csv(first, [1, 1], ["05-02-2010", "12-02-2010"], [100.0, 150.0])
    load_weekly(str(first), db_url)

    # The second import repeats both weeks and adds store 2 in the first week.
    second = tmp_path / "second.csv"
    write_weekly_csv(second, [1, 1, 2], ["05-02-2010", "12-02-2010", "05-02-2010"], [100.0, 150.0, 200.0])
    load_weekly(str(second), db_url)

    # The table must hold exactly three rows: the two from the first import plus the new one.
    engine = create_engine(db_url)
    with engine.connect() as conn:
        count = conn.execute(text("select count(*) from weekly_store_sales")).scalar()
        rows = pd.read_sql_query(text("select store, date, weekly_sales from weekly_store_sales"), conn)
    assert count == 3
    # The new row was written with its own sales value.
    assert rows.loc[rows["store"] == 2, "weekly_sales"].iloc[0] == 200.0

    # The loader must say how many rows it added and how many it skipped.
    # capsys reads what the loader printed during this test.
    printed = capsys.readouterr().out
    assert "Added 1 rows, skipped 2 duplicate rows." in printed


# A file that repeats a store and date inside itself must only add one of those rows.
def test_load_weekly_drops_repeats_inside_one_file(tmp_path, capsys):
    # The same store and week twice, plus one other week.
    csv_path = tmp_path / "weekly.csv"
    write_weekly_csv(csv_path, [1, 1, 1], ["05-02-2010", "05-02-2010", "12-02-2010"], [100.0, 100.0, 150.0])
    db_url = f"sqlite:///{tmp_path / 'weekly.db'}"
    load_weekly(str(csv_path), db_url)

    # Two different weeks means two rows.
    engine = create_engine(db_url)
    with engine.connect() as conn:
        count = conn.execute(text("select count(*) from weekly_store_sales")).scalar()
    assert count == 2
    # The repeat was counted as skipped, so the numbers add up to the cleaned rows.
    assert "Added 2 rows, skipped 1 duplicate rows." in capsys.readouterr().out


# Loading the very same file twice must leave the table unchanged.
def test_load_weekly_second_import_of_same_file_adds_nothing(tmp_path, capsys):
    csv_path = tmp_path / "weekly.csv"
    write_weekly_csv(csv_path, [1, 1], ["05-02-2010", "12-02-2010"], [100.0, 150.0])
    db_url = f"sqlite:///{tmp_path / 'weekly.db'}"
    # The same file, imported twice.
    load_weekly(str(csv_path), db_url)
    load_weekly(str(csv_path), db_url)

    # Still two rows, not four.
    engine = create_engine(db_url)
    with engine.connect() as conn:
        count = conn.execute(text("select count(*) from weekly_store_sales")).scalar()
    assert count == 2
    # The second run reports that it wrote nothing.
    assert "Added 0 rows, skipped 2 duplicate rows." in capsys.readouterr().out
