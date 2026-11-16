# coding=utf-8
"""Small helpers shared by the Disqus spiders.

The spiders stay responsible for one step each (``url_grabber`` finds the
Disqus iframe, ``json_grabber`` saves the thread payload). These helpers are
only plumbing:

* ``write_status`` / ``read_status`` - a JSON status file is how a spider
  reports its own outcome to ``collect.py``. Scrapy still exits with code 0
  when a callback raises, so its exit code cannot distinguish success from
  failure.
* ``prepare_output`` - remove a stale ``url.txt`` / ``thread.json`` so that a
  previous article's output can never be mistaken for the current one.
"""

import json
from pathlib import Path

import scrapy


def write_status(path, **payload):
    """Write a JSON status file if *path* was provided."""
    if not path:
        return
    status_path = Path(path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def read_status(path):
    """Return a status file as a dict, or ``None`` if it is missing/invalid."""
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def prepare_output(path, default_name):
    """Return the output path and make sure it starts empty."""
    output = Path(path) if path else Path(default_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.unlink()
    except FileNotFoundError:
        pass
    return output


def failure_message(failure):
    """Human-readable text for a Twisted ``Failure``."""
    value = getattr(failure, "value", None)
    if value is None:
        return str(failure)
    return "%s: %s" % (type(value).__name__, value)


class ErrorReportingSpider(scrapy.Spider):
    """Spider base class that reports request failures to a status file."""

    status_path = None
    source_url = None

    async def start(self):
        """Yield the requests built by :meth:`start_requests`.

        Scrapy >= 2.13 uses ``start()`` and ignores a plain ``start_requests``
        override, which would silently drop the errback that records failures.
        Older versions still call ``start_requests`` directly.
        """
        for request in self.start_requests():
            yield request

    def start_requests(self):
        raise NotImplementedError

    def on_error(self, failure):
        message = failure_message(failure)
        self.logger.error("request failed: %s", message)
        write_status(
            self.status_path, status="error", url=self.source_url, error=message
        )
        return None
