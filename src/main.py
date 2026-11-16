from scrape import scrape, authenticated_session
from helpers import send_request
from terms import BASE, discover_terms
import config

import json
import sys
import time
import os
import cProfile
import argparse
import emailer
import db
from diagnostics import QuarterFailures, format_failure, persist_failure


class CatalogueError(RuntimeError):
    """The TMS quarter catalogue could not be obtained; nothing was scraped."""


def load_catalogue(session) -> dict[str, str]:
    """Fetch and parse the TMS quarter catalogue before any per-quarter work.

    The catalogue gates every quarter, so a failure here must stop the run
    loudly instead of letting an empty selection masquerade as success.
    """
    try:
        html = send_request(session, BASE).text
    except Exception as exc:
        raise CatalogueError(
            "Could not download the TMS quarter catalogue; "
            "no quarters were scraped this run"
        ) from exc
    try:
        catalogue = discover_terms(html)
    except ValueError as exc:
        raise CatalogueError(
            "TMS quarter catalogue parsed empty; no quarters were scraped this run"
        ) from exc
    if not catalogue:
        raise CatalogueError(
            "TMS quarter catalogue is empty; no quarters were scraped this run"
        )
    return catalogue


def select_terms(args: argparse.Namespace, catalogue: dict[str, str]) -> list[str]:
    if args.all_quarters:
        return list(catalogue)
    code = args.term or config.year + config.quarter
    if code not in catalogue:
        available = ", ".join(
            f"{term} ({label})" for term, label in catalogue.items()
        )
        raise ValueError(
            f"Requested quarter {code} is not in the TMS catalogue; "
            f"TMS currently advertises: {available}"
        )
    return [code]


def main(args: argparse.Namespace) -> None:
    start_time = time.time()

    if args.db or args.migrate_only:
        db.initialize(config.year + config.quarter)
    if args.migrate_only:
        print("Database migration complete")
        return
    session = authenticated_session()
    catalogue = load_catalogue(session)
    if args.db:
        db.update_catalogue(catalogue, config.year + config.quarter)
    selected = select_terms(args, catalogue)
    failures: dict[str, str] = {}
    for term_index, code in enumerate(selected, start=1):
        print(
            f"Scraping {catalogue[code]} ({code}) ({term_index}/{len(selected)})",
            flush=True,
        )
        try:
            data = scrape(
                include_ratings=args.ratings,
                all_colleges=args.all_colleges,
                term_code=code,
                session=session,
                term_index=term_index,
                term_count=len(selected),
                term_name=catalogue[code],
            )
            if not data:
                raise ValueError(f"No data for {code}; retaining existing quarter")
            print(f"Found {len(data)} sections for {code}", flush=True)
            if not args.no_file:
                filename = (
                    f"{os.path.splitext(args.output_file)[0]}-{code}.json"
                    if args.all_quarters
                    else args.output_file
                )
                with open(filename, "w") as f:
                    json.dump(data, f, indent=4)
            if args.db:
                db.populate_db(data, code)
                print(f"Published {code}", flush=True)
        except Exception as exc:
            if not args.all_quarters:
                raise
            detail = format_failure(exc)
            failures[code] = detail
            print(
                f"Failed {code}; continuing with remaining quarters\n{detail}",
                flush=True,
            )
            # Save immediately, even if the job is terminated before the loop ends.
            persist_failure(f"Quarter {code}:\n{detail}")

    if failures:
        raise QuarterFailures(failures)

    print("--- {} seconds ---".format(time.time() - start_time))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="python3 src/main.py",
        description="Scrape data from Drexel Term Master Schedule and save it to a JSON file.",
    )
    quarters = parser.add_mutually_exclusive_group()
    quarters.add_argument(
        "--all-quarters",
        action="store_true",
        help="Collect every quarter currently advertised by TMS",
    )
    quarters.add_argument("--term", help="Collect one TMS quarter code, e.g. 202625")
    parser.add_argument(
        "--migrate-only",
        action="store_true",
        help="Initialize/migrate the database without scraping",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        metavar="FILE",
        action="store",
        default="data.json",
        help="File to write the data to (include the .json extension in the file name)",
    )
    parser.add_argument(
        "--ratings",
        action="store_true",
        help="Include Rate My Professor ratings in the data",
    )
    parser.add_argument(
        "--all-colleges",
        action="store_true",
        help="Include all colleges in the data, not just the one in the config.py file",
    )
    parser.add_argument(
        "--no-file",
        action="store_true",
        help="Do not write the data to a file",
    )
    parser.add_argument(
        "--db",
        action="store_true",
        help="Update the database with the scraped data, or create a new one if it doesn't exist",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Send an email if an exception occurs (requires DREXEL_SCHEDULER_TOPIC_ARN environment variable to be set to the ARN of an AWS SNS topic)",
    )

    args = parser.parse_args()

    try:
        if not os.path.exists("performance"):
            os.makedirs("performance")
        cProfile.run("main(args)", "performance/profile_output.pstat")
    except Exception as exc:
        trace = format_failure(exc)
        print(trace)
        persist_failure(trace)

        if args.email:
            environment = os.environ.get("ENVIRONMENT", "UNKNOWN")
            if emailer.send_email(f"{environment}: Error running scraper", trace):
                print("Exception email sent")
            else:
                print("Error sending exception email")
        sys.exit(1)
