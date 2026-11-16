# coding=utf-8
import json

import scrapy
from scrapy.http.headers import Headers

from .common import ErrorReportingSpider, prepare_output, write_status

# Define the Splash API endpoint
RENDER_HTML_URL = "http://localhost:8050/render.html"


class UrlGrabberSpider(ErrorReportingSpider):
    name = "url_grabber"

    # Any of the selectors historically documented in this project can identify
    # the Disqus iframe: its URL, its title, or its ``dsq`` element id.
    IFRAME_XPATH = (
        "//iframe["
        "contains(@src,'http://disqus.com') or "
        "contains(@src,'https://disqus.com') or "
        "contains(@src,'//disqus.com') or "
        "@title='Disqus' or "
        "contains(@id,'dsq')]"
    )

    # the crawler takes an URL in input
    # so we put that in the initializer
    # ``out`` (default url.txt) and ``status`` are optional; they let the
    # collection runner keep one url.txt per article and read the outcome.
    def __init__(self, url=None, out=None, status=None, **kwargs):
        super().__init__(**kwargs)
        if not url:
            raise ValueError("url_grabber requires -a url=<article url>")
        self.source_url = url
        self.start_urls = [url]
        self.output_path = prepare_output(out, "url.txt")
        self.status_path = status

    # Splash is the default (real blog pages render through it), but local
    # fixtures and Splash-less setups fetch the page directly. Settings are
    # only available once the spider is bound to a crawler (i.e. not in
    # __init__), hence this is evaluated when the crawl starts.
    def _use_splash(self):
        if self.source_url.lower().startswith(
            ("file:", "data:", "http://localhost", "http://127.0.0.1")
        ):
            return False
        return self.settings.getbool("SPLASH_ENABLED", True)

    # We call the Splash API as a middleware to render the desired URL.
    # In this case we don't want the page to execute all the javascript,
    # so we set the js_enabled parameter to False.
    # Local fixtures (file://) and setups without Splash are fetched directly,
    # which is equivalent because the page was never JS-rendered anyway.
    def start_requests(self):
        if self._use_splash():
            for url in self.start_urls:
                body = json.dumps({"url": url, "wait": 0.5, "js_enabled": False})
                headers = Headers({"Content-Type": "application/json"})
                yield scrapy.Request(
                    RENDER_HTML_URL,
                    self.parse,
                    method="POST",
                    body=body,
                    headers=headers,
                    errback=self.on_error,
                    dont_filter=True,
                )
            return

        for url in self.start_urls:
            yield scrapy.Request(
                url, self.parse, errback=self.on_error, dont_filter=True
            )

    # Then, as a callback from the Splash request, we actually use scrapy and
    # the Xpath syntax to extract information: the URL of the Disqus iframe.
    # It is written to ``url.txt`` (or the ``out`` given by the caller).
    def parse(self, response, **kwargs):
        iframes = response.xpath(self.IFRAME_XPATH)
        srcs = [src.strip() for src in iframes.xpath("@src").extract() if src.strip()]

        if not srcs:
            self.logger.warning("no Disqus iframe found on %s", self.source_url)
            write_status(
                self.status_path,
                status="no_iframe",
                url=self.source_url,
                detail="no Disqus iframe found in the article page",
            )
            return

        src = srcs[0]
        if src.startswith("//"):
            # Protocol-relative iframe address: resolve it against https://
            # (urljoin would otherwise inherit file:// from a local fixture).
            iframe_url = "https:" + src
        else:
            iframe_url = response.urljoin(src)

        self.output_path.write_text(iframe_url, encoding="utf-8")
        write_status(
            self.status_path,
            status="found",
            url=self.source_url,
            iframe=iframe_url,
            out=str(self.output_path),
        )
