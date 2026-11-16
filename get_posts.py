"""Parse a Disqus ``thread.json`` payload and store its posts in MongoDB.

This module keeps the original responsibility of ``get_posts.py``:

* read the JSON returned by the Disqus embed endpoint,
* extract ``response.posts``,
* write the posts to the ``disqus.comments`` collection.

It can still be used as a standalone CLI (``python get_posts.py`` reads
``thread.json`` from the current directory, as before), but the parsing and
storage primitives are also importable so that a collection orchestrator can
reuse them without shelling out.

Writes are idempotent: every document is upserted on the comment ``id`` (with
a content hash fallback for payloads without ``id``), and unique indexes back
that up, so running the same article twice does not create duplicate comments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

DEFAULT_THREAD_FILE = "thread.json"
DEFAULT_MONGO_URI = "mongodb://localhost:27017"
DEFAULT_DB = "disqus"
DEFAULT_COLLECTION = "comments"

# Name of the sparse unique indexes used to guarantee idempotent writes.
COMMENT_ID_INDEX = "comment_id_unique"
COMMENT_FALLBACK_INDEX = "comment_fallback_unique"


class ThreadJsonError(ValueError):
    """The thread payload is missing, malformed, or has an unexpected shape."""


class CommentWriteError(RuntimeError):
    """The parsed comments could not be written to MongoDB."""


def parse_posts_from_text(json_text: str) -> List[Dict[str, Any]]:
    """Return ``response.posts`` from a raw Disqus thread payload.

    A JSON document whose ``response.posts`` is an empty list is valid and
    yields ``[]`` (a successfully collected article with zero comments).

    Raises :class:`ThreadJsonError` for undecodable JSON, a missing/non-list
    ``posts`` value, or posts that are not JSON objects.
    """
    if json_text is None:
        raise ThreadJsonError("thread data is empty")
    text = json_text.strip()
    if not text:
        raise ThreadJsonError("thread data is empty")

    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ThreadJsonError("thread data is not valid JSON: %s" % exc) from exc

    if not isinstance(payload, dict):
        raise ThreadJsonError("thread data root is not a JSON object")

    response = payload.get("response")
    if not isinstance(response, dict):
        raise ThreadJsonError('thread data has no "response" object')

    posts = response.get("posts")
    if not isinstance(posts, list):
        raise ThreadJsonError('thread data has no "response.posts" list')

    parsed: List[Dict[str, Any]] = []
    for index, post in enumerate(posts):
        if not isinstance(post, dict):
            raise ThreadJsonError(
                "post at index %d is %s, expected a JSON object"
                % (index, type(post).__name__)
            )
        parsed.append(post)
    return parsed


def load_posts(path: str | Path) -> List[Dict[str, Any]]:
    """Read *path* and return its parsed posts (``thread.json`` by default)."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ThreadJsonError("cannot read %s: %s" % (file_path, exc)) from exc
    return parse_posts_from_text(text)


def _comment_identity(post: Mapping[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the upsert filter and the document for one Disqus post.

    Disqus posts normally carry a stable ``id``. Payloads without one fall
    back to a deterministic SHA-1 of the canonical JSON, stored as
    ``_comment_key`` so repeated collections still update instead of duplicate.
    """
    post_id = post.get("id")
    if post_id is not None and str(post_id) != "":
        return {"id": post_id}, dict(post)

    canonical = json.dumps(
        post, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
    document = dict(post)
    document["_comment_key"] = digest
    return {"_comment_key": digest}, document


class MongoCommentStore:
    """Idempotent writer for Disqus comments in MongoDB.

    ``client`` can be injected (``pymongo.MongoClient`` or a test double such
    as ``mongomock.MongoClient``); otherwise a client is created from ``uri``.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        db_name: str = DEFAULT_DB,
        collection_name: str = DEFAULT_COLLECTION,
        client: Any = None,
    ) -> None:
        self.db_name = db_name
        self.collection_name = collection_name
        self._indexes_ready = False

        if client is None:
            try:
                from pymongo import MongoClient
            except ImportError as exc:  # pragma: no cover - dependency missing
                raise CommentWriteError(
                    "pymongo is required to write comments: %s" % exc
                ) from exc
            try:
                client = MongoClient(uri or DEFAULT_MONGO_URI, serverSelectionTimeoutMS=5000)
            except Exception as exc:
                raise CommentWriteError("cannot create MongoDB client: %s" % exc) from exc

        self.client = client
        # Resolved lazily so an article with an empty posts list succeeds even
        # when MongoDB is unreachable: nothing needs to be written or counted.
        self._db = None
        self._collection = None

    @property
    def db(self):
        if self._db is None:
            try:
                self._db = self.client[self.db_name]
            except Exception as exc:
                raise CommentWriteError("cannot access MongoDB database: %s" % exc) from exc
        return self._db

    @property
    def collection(self):
        if self._collection is None:
            try:
                self._collection = self.db[self.collection_name]
            except CommentWriteError:
                raise
            except Exception as exc:
                raise CommentWriteError(
                    "cannot access MongoDB collection: %s" % exc
                ) from exc
        return self._collection

    def ensure_indexes(self) -> None:
        """Create the unique indexes that enforce idempotent upserts."""
        if self._indexes_ready:
            return
        try:
            self.collection.create_index(
                "id", unique=True, sparse=True, name=COMMENT_ID_INDEX
            )
            self.collection.create_index(
                "_comment_key",
                unique=True,
                sparse=True,
                name=COMMENT_FALLBACK_INDEX,
            )
        except Exception as exc:
            raise CommentWriteError("cannot create comment indexes: %s" % exc) from exc
        self._indexes_ready = True

    def upsert_posts(
        self, posts: Iterable[Mapping[str, Any]]
    ) -> Dict[str, int]:
        """Write *posts* idempotently and return per-run counters.

        Returned keys: ``comments`` (number of posts seen), ``inserted``
        (new documents), ``updated`` (existing documents whose content
        changed), ``unchanged`` (existing documents already up to date).
        """
        post_list = list(posts)
        counters = {
            "comments": len(post_list),
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
        }
        if not post_list:
            # Nothing to persist. An empty thread is a successful, zero-comment
            # article, so do not touch (or require) MongoDB at all.
            return counters

        self.ensure_indexes()
        try:
            for post in post_list:
                if not isinstance(post, Mapping):
                    raise ThreadJsonError(
                        "post is %s, expected a JSON object" % type(post).__name__
                    )
                filter_doc, document = _comment_identity(post)
                result = self.collection.update_one(
                    filter_doc, {"$set": document}, upsert=True
                )
                if getattr(result, "upserted_id", None) is not None:
                    counters["inserted"] += 1
                elif getattr(result, "modified_count", 0):
                    counters["updated"] += 1
                else:
                    counters["unchanged"] += 1
        except ThreadJsonError:
            raise
        except Exception as exc:
            raise CommentWriteError("cannot write comments to MongoDB: %s" % exc) from exc

        return counters

    def count_comments(self) -> int:
        """Return how many comment documents are currently stored."""
        try:
            return int(self.collection.count_documents({}))
        except Exception as exc:
            raise CommentWriteError("cannot count comments: %s" % exc) from exc

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store Disqus thread.json posts in MongoDB (idempotent)."
    )
    parser.add_argument(
        "thread_file",
        nargs="?",
        default=DEFAULT_THREAD_FILE,
        help="path to the Disqus thread JSON file (default: %(default)s)",
    )
    parser.add_argument("--mongo-uri", default=None, help="MongoDB connection URI")
    parser.add_argument("--db", default=DEFAULT_DB, help="database name (default: %(default)s)")
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help="collection name (default: %(default)s)",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="optional path to write the JSON summary to",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        posts = load_posts(args.thread_file)
    except ThreadJsonError as exc:
        print("INVALID THREAD JSON: %s" % exc, file=sys.stderr)
        return 2

    store = None
    try:
        store = MongoCommentStore(
            uri=args.mongo_uri, db_name=args.db, collection_name=args.collection
        )
        counters = store.upsert_posts(posts)
        total = store.count_comments()
    except CommentWriteError as exc:
        print("WRITE FAILED: %s" % exc, file=sys.stderr)
        return 3
    finally:
        if store is not None:
            store.close()

    summary = {
        "thread_file": str(Path(args.thread_file)),
        "db": args.db,
        "collection": args.collection,
        "comments": counters["comments"],
        "inserted": counters["inserted"],
        "updated": counters["updated"],
        "unchanged": counters["unchanged"],
        "total_in_collection": total,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.report:
        Path(args.report).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
