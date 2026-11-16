# Sample fixtures

These files let you exercise the whole collection pipeline without network
access (no blog, no Splash, no Disqus):

| Article | Embed page | Expected outcome |
| --- | --- | --- |
| `articles/article-ok.html` (3 comments) | `embed/thread-ok.html` | `success`, 3 comments |
| `articles/article-empty-thread.html` | `embed/thread-empty.html` | `success`, 0 comments |
| `articles/article-no-disqus.html` | - | `no_iframe` |
| `articles/article-corrupt-thread.html` | `embed/thread-corrupt.html` | `thread_json_invalid` |
| `articles/article-missing-thread.html` | `embed/thread-missing.html` | `thread_json_missing` |

The article pages embed their Disqus iframe with a relative `src`, so the same
files work from any checkout location.

## Run the acceptance script

```bash
# in-memory MongoDB (mongomock) - default, nothing to install or clean up
python samples/run_sample_acceptance.py

# against a real MongoDB
python samples/run_sample_acceptance.py --mongo-uri mongodb://localhost:27017 --db disqus_samples
```

The script collects the five articles above in one run, prints each article's
outcome and comment count, then collects `article-ok.html` a second time to
show that the write is idempotent (`inserted=0 unchanged=3`, still 3 comments).
It exits 0 only when every check passes.

## Run the samples through main.sh

```bash
cat > /tmp/sample-articles.txt <<EOF
file://$(pwd)/samples/articles/article-ok.html
file://$(pwd)/samples/articles/article-no-disqus.html
file://$(pwd)/samples/articles/article-corrupt-thread.html
EOF

PYTHON=python ARTICLES_FILE=/tmp/sample-articles.txt \
  MONGO_URI=mongodb://localhost:27017 MONGO_DB=disqus_samples \
  sh main.sh
```

Per-article artifacts (`url.txt`, `thread.json`, and the two `*.status.json`
files that record each step's outcome) are written under `collect-work/`.
