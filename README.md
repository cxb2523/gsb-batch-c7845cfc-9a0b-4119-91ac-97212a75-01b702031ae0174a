# disqus-crawler

> Crawl DISQUS comments from a blog into a local MongoDB database

## Installation

- Clone the github repository and cd into it

```bash
git clone git@github.com:louisguitton/disqus-crawler.git
cd disqus-crawler
python3 -m venv venv
source venv/bin/activate
pip install --upgrade -r requirements.txt
```

## Usage example

- List the blog articles you want to crawl in `articles.txt`, one URL per line
  (blank lines and `#` comments are ignored)

- Make sure a mongod instance is running on your computer (Installation instructions for MongoDB are [here](https://docs.mongodb.com/manual/tutorial/install-mongodb-on-os-x/))

```bash
mongod --config /usr/local/etc/mongod.conf
```

- Make sure a splash instance is running [(more information here)](https://github.com/scrapinghub/scrapy-splash)

```bash
$ docker run -p 8050:8050 scrapinghub/splash
2019-10-10 12:03:39.116598 [-] Server listening on http://0.0.0.0:8050
```

- Run the main.sh script

```bash
$ sh main.sh
ARTICLE http://www.purseblog.com/louis-vuitton/louis-vuitton-spring-2016-bag-ad-campaign/ -> success  comments=42 (inserted=42 updated=0 unchanged=0)
ARTICLE http://www.purseblog.com/louis-vuitton/no-comments-here/ -> no_iframe (no Disqus iframe found in the article page)
...

SUMMARY
  http://www.purseblog.com/louis-vuitton/louis-vuitton-spring-2016-bag-ad-campaign/ success  comments=42 (inserted=42 updated=0 unchanged=0)
  http://www.purseblog.com/louis-vuitton/no-comments-here/                       no_iframe (no Disqus iframe found in the article page)
  1/2 articles succeeded, 1 failed; 42 comments in disqus.comments
```

Every article settles on its own outcome and a failure never stops the rest of
the run. `MONGO_URI` / `MONGO_DB` override the default MongoDB target:

```bash
MONGO_URI=mongodb://localhost:27017 MONGO_DB=disqus sh main.sh --report report.json
```

- Usage

```bash
mongo
```

```mongodb
use disqus
db.comments.count()
db.comments.find().pretty().limit(2)
```

## How it works

The original three steps are unchanged in their responsibilities:

| Step | File | Responsibility |
| --- | --- | --- |
| `url_grabber` | `disqus-crawler/spiders/url_grabber.py` | find the Disqus iframe address on an article page (`url.txt`) |
| `json_grabber` | `disqus-crawler/spiders/json_grabber.py` | fetch the embed page and save its thread payload (`thread.json`) |
| `get_posts.py` | `get_posts.py` | parse `response.posts` and write them to MongoDB |

`collect.py` is the reusable collection entry point between them. For each
article it runs those steps in order and settles the article on exactly one
outcome:

| Outcome | Meaning |
| --- | --- |
| `success` | comments were written; `comments=0` is a valid success |
| `no_iframe` | the article page has no Disqus iframe |
| `thread_json_missing` | the embed page has no `disqus-threadData` payload |
| `thread_json_invalid` | the payload is not valid JSON / has no `response.posts` |
| `write_failed` | MongoDB rejected the write |
| `request_failed` | fetching the article page or the embed page failed |

A failure on one article never stops the others. Comments are written per
article as soon as they are parsed, so a later failure cannot lose earlier
articles' comments.

Writes are idempotent: comments are upserted on their Disqus `id` (with a
content-hash fallback for payloads without `id`) behind sparse unique indexes,
so collecting the same article twice updates instead of duplicating.

Each article gets its own directory under `collect-work/` (override with
`--work-dir`), so a stale `url.txt` / `thread.json` from another article can
never be read by mistake.

Programmatic use:

```python
import collect, get_posts

store = get_posts.MongoCommentStore(uri="mongodb://localhost:27017")
results = collect.collect_articles(["https://blog/post-1", "https://blog/post-2"], store)
for result in results:
    print(result.article_url, result.outcome, result.comments)
```

## Local sample run

`samples/` contains offline fixtures for every outcome plus a runner that uses
an in-memory MongoDB (`mongomock`) by default:

```bash
python samples/run_sample_acceptance.py
```

It collects these files and prints one outcome per article:

| Sample article | Expected outcome |
| --- | --- |
| `samples/articles/article-ok.html` (3 comments) | `success`, 3 comments |
| `samples/articles/article-empty-thread.html` | `success`, 0 comments |
| `samples/articles/article-no-disqus.html` | `no_iframe` |
| `samples/articles/article-corrupt-thread.html` | `thread_json_invalid` |
| `samples/articles/article-missing-thread.html` | `thread_json_missing` |

It then collects `article-ok.html` a second time to prove idempotency: the
second pass reports `inserted=0 unchanged=3`, and the collection still holds
3 comments. To run the same flow against a real MongoDB:

```bash
python samples/run_sample_acceptance.py --mongo-uri mongodb://localhost:27017 --db disqus_samples
```

The regression tests run entirely offline:

```bash
python -m pytest tests
```

Requirements: `mongomock` is only needed for the default sample run and the
tests (`pip install mongomock pytest`); `scrapy-splash` is optional and only
used when it can be imported, otherwise article pages are fetched directly.

## Meta

I wrote this project for my master thesis in 2016 on Paid/Owned/Earned Media, and measuring brands on social channels and blogs.

For the crawling, this project uses [scrapy](http://scrapy.org/).
It stores the comments in a MongoDB database, using the pymongo client.
A good tutorial to follow is [this one](https://realpython.com/blog/python/web-scraping-with-scrapy-and-mongodb/).

When scrapping the web, 2 kinds of problems arise:

- the target page is too slow to render because it uses a lot of javascript
- the target page renders everything really fast but what you were interested in was something that disappears when the page is rendered

To overcome these situations, one can deploy a tiny web-browser on a local machine
that will render the pages at his will.
This project uses Splash, on a local Docker container.
A good tutorial to follow is [this one](http://blog.scrapinghub.com/2015/03/02/handling-javascript-in-scrapy-with-splash/).
