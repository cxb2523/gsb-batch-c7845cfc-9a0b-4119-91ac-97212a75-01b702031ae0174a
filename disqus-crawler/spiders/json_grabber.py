# coding=utf-8
import scrapy

from .common import ErrorReportingSpider, prepare_output, write_status


class JsonGrabberSpider(ErrorReportingSpider):
    name = "json_grabber"

    # The Disqus embed page carries the thread payload in this script tag.
    THREAD_DATA_XPATH = "//script[@id='disqus-threadData']/text()"

    # the crawler takes an URL in input
    # this URL is the output of the UrlGrabberSpider in getDisqusUrl.py
    # ``out`` (default thread.json) and ``status`` are optional; they let the
    # collection runner keep one thread.json per article and read the outcome.
    def __init__(self, url=None, out=None, status=None, **kwargs):
        super().__init__(**kwargs)
        if not url:
            raise ValueError("json_grabber requires -a url=<disqus iframe url>")
        self.source_url = url
        self.start_urls = [url]
        self.output_path = prepare_output(out, "thread.json")
        self.status_path = status

    # We use scrapy and the Xpath syntax to extract the thread payload and
    # store it in a file called thread.json (or the ``out`` given by caller).
    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(
                url, self.parse, errback=self.on_error, dont_filter=True
            )

    def parse(self, response, **kwargs):
        payloads = response.xpath(self.THREAD_DATA_XPATH).extract()
        payload = payloads[0] if payloads else ""

        if not payload.strip():
            self.logger.warning("no disqus-threadData payload at %s", self.source_url)
            write_status(
                self.status_path,
                status="missing",
                url=self.source_url,
                detail="embed page has no disqus-threadData payload",
            )
            return

        self.output_path.write_text(payload, encoding="utf-8")
        write_status(
            self.status_path,
            status="saved",
            url=self.source_url,
            out=str(self.output_path),
            bytes=len(payload.encode("utf-8")),
        )
