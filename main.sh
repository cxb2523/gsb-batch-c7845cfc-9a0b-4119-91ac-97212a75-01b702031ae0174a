#!/bin/sh
# Choose the blog articles you want to crawl
# NOTE: the blog should be powered by DISQUS
#
# One article URL per line in articles.txt. Articles are collected one by one
# and each one settles on its own outcome (success / no_iframe /
# thread_json_missing / thread_json_invalid / write_failed / request_failed);
# a failure on one article does not stop the others.
#
# For a fully local run (no network, no blog), list the bundled samples instead,
# e.g. file:///<path-to-repo>/samples/articles/article-ok.html
# or simply run:  python samples/run_sample_acceptance.py

ARTICLES_FILE="${ARTICLES_FILE:-articles.txt}"
WORK_DIR="${WORK_DIR:-collect-work}"

if [ ! -f "$ARTICLES_FILE" ]; then
    echo "Missing $ARTICLES_FILE - add one article URL per line." >&2
    echo "Tip: run 'python samples/run_sample_acceptance.py' for a local demo." >&2
    exit 2
fi

# Collect the comments article by article.
#
# collect.py drives the two existing Scrapy spiders step by step:
#   url_grabber  - get the API call from the blog post to DISQUS (url.txt)
#   json_grabber - get the JSON the API sends back (thread.json)
# then hands the JSON to get_posts.py's parser + idempotent MongoDB writer.
#
# Each article's outcome is printed as it settles; the process keeps going
# even when an article fails. Set MONGO_URI to write to a non-default MongoDB.
if [ -z "${PYTHON:-}" ]; then
    if [ -x "venv/bin/python" ]; then
        PYTHON="venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON="python3"
    else
        PYTHON="python"
    fi
fi
"$PYTHON" collect.py \
    --urls-file "$ARTICLES_FILE" \
    --work-dir "$WORK_DIR" \
    ${MONGO_URI:+--mongo-uri "$MONGO_URI"} \
    ${MONGO_DB:+--db "$MONGO_DB"} \
    "$@"
