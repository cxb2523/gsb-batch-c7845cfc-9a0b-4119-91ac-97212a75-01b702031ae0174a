# -*- coding: utf-8 -*-
"""Earnings leaderboard entry point.

The scraping pipeline itself lives in ``umg_scrape.py`` and is importable and
testable on its own; this script only wires it to the real database session.
Run it as before:

    python earnings_scrape.py
"""

import sys


def main():
    from db.base_sql import Session, engine
    from db.top_earning_players_sql import Earning_Player
    from umg_scrape import scrape_earnings

    # 1 generate database schema
    # 2 Create a new session
    try:
        Earning_Player.metadata.create_all(engine)
        session = Session()
    except Exception as exc:
        print(f"Scraping failed: could not connect to the database: {exc}")
        return 1
    try:
        result = scrape_earnings(session)
    finally:
        session.close()

    if not result.ok:
        print(f"Scraping failed: {result.error}")
        return 1
    for failure in result.failed:
        print(f"skipped {failure}")
    print(f"Done: {result.inserted} rows inserted, {result.failed_count} rows skipped.")
    return 0


if __name__ == "__main__":
    print("Scraping data")
    sys.exit(main())
