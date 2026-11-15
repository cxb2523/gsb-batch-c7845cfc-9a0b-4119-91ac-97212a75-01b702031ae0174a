"""
BigQuery Analytics & Reporting — entry point.

Runs four analytics and reporting demos in sequence:
  1. Simple query   — top words in Shakespeare, verifies auth/client
  2. Parameterized  — same query with named params, injection-safe
  3. DataFrame      — query results exported to pandas for last-mile analysis
  4. Metadata       — dataset/table inventory walk with storage stats

Usage:
    python main.py
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

from queries import run_simple_query, run_parameterized_query
from reporting import query_to_dataframe, list_datasets_tables_metadata

SEPARATOR = "=" * 60


def section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def main():
    section("1. Simple Query — Top Shakespeare Words")
    run_simple_query()

    section("2. Parameterized Query — Hamlet, min length 6")
    run_parameterized_query(min_length=6, corpus="hamlet", row_limit=10)

    section("3. Query Results to pandas DataFrame")
    query_to_dataframe()

    section("4. Dataset & Table Metadata Listing")
    list_datasets_tables_metadata()

    print(f"\n{SEPARATOR}")
    print("All analytics and reporting demos complete.")


if __name__ == "__main__":
    main()
