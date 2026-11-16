#!/usr/bin/env python
# coding=utf-8
"""End-to-end acceptance run over the local sample articles (no network needed).

The samples live next to this script and exercise every per-article outcome:

* ``samples/articles/article-ok.html``             - 3 comments  -> success
* ``samples/articles/article-empty-thread.html``   - 0 comments  -> success (0)
* ``samples/articles/article-no-disqus.html``      - no iframe   -> no_iframe
* ``samples/articles/article-corrupt-thread.html`` - bad JSON    -> thread_json_invalid
* ``samples/articles/article-missing-thread.html`` - no payload  -> thread_json_missing

It also collects ``article-ok.html`` twice to prove idempotency: the second
pass reports ``inserted=0`` and the collection still holds 3 comments.

By default the run uses an in-memory MongoDB (``mongomock``) so it never
touches a real database. Pass ``--mongo-uri`` (and optionally ``--db`` /
``--collection``) to run the very same flow against MongoDB instead.

    python run_sample_acceptance.py
    python run_sample_acceptance.py --mongo-uri mongodb://localhost:27017 --db disqus_samples
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import collect  # noqa: E402
import get_posts  # noqa: E402


def file_url(path: Path) -> str:
    return path.resolve().as_uri()


def make_store(args):
    if args.mongo_uri:
        return get_posts.MongoCommentStore(
            uri=args.mongo_uri, db_name=args.db, collection_name=args.collection
        )
    try:
        import mongomock
    except ImportError:
        raise SystemExit(
            "mongomock is not installed; pass --mongo-uri to use a real MongoDB"
        )
    return get_posts.MongoCommentStore(
        db_name=args.db, collection_name=args.collection, client=mongomock.MongoClient()
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongo-uri", default=None, help="use a real MongoDB instead of mongomock")
    parser.add_argument("--db", default="disqus_samples")
    parser.add_argument("--collection", default="comments")
    parser.add_argument("--work-dir", default="samples-work")
    args = parser.parse_args(argv)

    articles_dir = HERE / "articles"
    ok_url = file_url(articles_dir / "article-ok.html")
    cases = [
        ("normal article", ok_url),
        ("empty thread (0 comments)", file_url(articles_dir / "article-empty-thread.html")),
        ("no Disqus iframe", file_url(articles_dir / "article-no-disqus.html")),
        ("corrupt thread JSON", file_url(articles_dir / "article-corrupt-thread.html")),
        ("embed page without payload", file_url(articles_dir / "article-missing-thread.html")),
    ]

    store = make_store(args)
    crawler = collect.ScrapyStepCrawler(log_level="ERROR")
    labels = {url: label for label, url in cases}

    print("=" * 100)
    print("PASS 1: one article per outcome, all in a single run")
    print("=" * 100)
    results = collect.collect_articles(
        [url for _, url in cases], store=store, work_dir=args.work_dir, crawler=crawler
    )
    for result in results:
        print(
            "%-32s %-70s %s"
            % (labels.get(result.article_url, "?"), result.article_url, result.summary_line())
        )

    print("")
    print("=" * 100)
    print("PASS 2: collect the normal article again (idempotency check)")
    print("=" * 100)
    second = collect.collect_articles(
        [ok_url], store=store, work_dir=args.work_dir, crawler=crawler
    )[0]
    print("%-32s %s" % ("normal article (2nd run)", second.summary_line()))

    total = store.count_comments()
    stored_ids = sorted(
        doc.get("id") for doc in store.collection.find({}, {"id": 1, "_id": 0})
    )
    store.close()

    print("")
    print("=" * 100)
    print("FINAL")
    print("=" * 100)
    print("comments in %s.%s: %d" % (args.db, args.collection, total))
    print("stored comment ids: %s" % stored_ids)

    expected_total = 3
    checks = [
        ("normal article succeeded with 3 comments", results[0].outcome == collect.SUCCESS and results[0].comments == 3),
        ("empty thread succeeded with 0 comments", results[1].outcome == collect.SUCCESS and results[1].comments == 0),
        ("missing iframe settled as no_iframe", results[2].outcome == collect.NO_IFRAME),
        ("corrupt JSON settled as thread_json_invalid", results[3].outcome == collect.THREAD_JSON_INVALID),
        ("missing payload settled as thread_json_missing", results[4].outcome == collect.THREAD_JSON_MISSING),
        ("nothing was written twice (3 comments total)", total == expected_total),
        ("second run inserted 0 comments", second.inserted == 0 and second.unchanged == 3),
    ]
    print("")
    failed = 0
    for label, passed in checks:
        print("  [%s] %s" % ("PASS" if passed else "FAIL", label))
        failed += 0 if passed else 1

    if args.work_dir:
        print("")
        print("per-article artifacts under: %s" % Path(args.work_dir).resolve())
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
