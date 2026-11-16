"""
Local version of ScraperWiki Utils, documentation here:
https://scraperwiki.com/docs/python/python_help_documentation/
"""

import os
import shutil
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings

from .errors import ScrapeHTTPError, ScrapeNetworkError, ScrapeTimeoutError


def scrape(url, params=None, user_agent=None, timeout=None):
    """
    Scrape a URL optionally with parameters.

    This is effectively a wrapper around urllib.request.urlopen, except that
    failures are reported as subclasses of :class:`scraperwiki.Error`:

    * a non-2xx HTTP response raises :class:`scraperwiki.ScrapeHTTPError`
      (which carries the status code),
    * a connection failure raises :class:`scraperwiki.ScrapeNetworkError`,
    * a timeout raises :class:`scraperwiki.ScrapeTimeoutError`.

    ``timeout`` is the number of seconds to wait for the connection and the
    response. ``None`` (the default) keeps the standard library's behaviour,
    which is to wait indefinitely.

    On success the raw body is returned as ``bytes``.
    """

    headers = {}

    if user_agent:
        headers["User-Agent"] = user_agent

    data = None
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return f.read()
    except urllib.error.HTTPError as e:
        raise ScrapeHTTPError(url, e.code, e.reason) from e
    except urllib.error.URLError as e:
        # socket.timeout is an alias of TimeoutError on Python 3.10+.
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise ScrapeTimeoutError(url, timeout) from e
        raise ScrapeNetworkError(url, e.reason) from e
    except TimeoutError as e:
        raise ScrapeTimeoutError(url, timeout) from e
    except OSError as e:
        # Connection resets and similar errors raised while reading the body.
        raise ScrapeNetworkError(url, e) from e


def pdftoxml(pdfdata, options=""):
    """converts pdf file to xml file"""
    if not shutil.which("pdftohtml"):
        warnings.warn(
            "scraperwiki.pdftoxml requires pdftohtml, but pdftohtml was not found "
            "in the PATH. If you wish to use this function, you probably need to "
            "install pdftohtml."
        )
        return None

    with (
        tempfile.NamedTemporaryFile(suffix=".pdf") as pdffout,
        tempfile.NamedTemporaryFile(mode="r", suffix=".xml", encoding="utf-8") as xmlin,
    ):
        pdffout.write(pdfdata)
        pdffout.flush()

        tmpxml = xmlin.name  # "temph.xml"
        cmd = f'pdftohtml -xml -nodrm -zoom 1.5 -enc UTF-8 -noframes {options} "{pdffout.name}" "{os.path.splitext(tmpxml)[0]}"'
        # can't turn off output, so throw away even stderr yeuch
        cmd = cmd + " >/dev/null 2>&1"
        os.system(cmd)

        xmldata = xmlin.read()

    return xmldata


def status(type, message=None):
    """Retained for backwards compatibility."""
    warnings.warn(
        "status() is no longer in use following ScraperWiki/Quickcode application shutdown",
        DeprecationWarning,
        stacklevel=2,
    )


def swimport(scrapername):
    return __import__(scrapername)
