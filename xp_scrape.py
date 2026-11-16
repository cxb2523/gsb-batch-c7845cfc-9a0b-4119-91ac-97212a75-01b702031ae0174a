# -*- coding: utf-8 -*-
"""XP leaderboard entry point.

The scraping pipeline itself lives in ``umg_scrape.py`` and is importable and
testable on its own; this script only wires it to the real database session.
Run it as before:

    python xp_scrape.py
"""

import sys


def main():
    from db.base_sql import Session, engine
    from db.player_sql import Player
    from umg_scrape import scrape_xp

    # 1 generate database schema
    # 2 Create a new session
    try:
        Player.metadata.create_all(engine)
        session = Session()
    except Exception as exc:
        print(f"Scraping failed: could not connect to the database: {exc}")
        return 1
    try:
        result = scrape_xp(session)
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
