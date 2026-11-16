from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from . import outcome
from .core import Link, Url


@dataclass(frozen=True)
class UrlInfo:
    result: outcome.Result
    links: Optional[Sequence[Link]]
    external: bool = False

    def link_urls(self) -> frozenset[Url]:
        # External addresses are only probed once (HEAD, falling back to GET on 405).
        # Their content is never parsed and redirects they return must not be followed,
        # so they never yield new URLs to crawl.
        if self.external:
            return frozenset()

        redirect_url = self.result.redirect_url()

        if redirect_url is not None:
            return frozenset([redirect_url])
        elif self.links is None:
            return frozenset()
        else:
            return frozenset(link.url for link in self.links)


@dataclass(frozen=True)
class UrlStore:
    url_infos: dict[Url, UrlInfo] = field(init=False, default_factory=dict)
    seen_urls: set[Url] = field(init=False, default_factory=set)

    def add_page(self, url: Url, info: UrlInfo) -> frozenset[Url]:
        """
        Store page information for a given URL and return new URLs.

        This can only be called once for each URL and each discovered URL is only returned
        once.
        """

        assert url not in self.url_infos, f"URL already stored: {url}"
        self.url_infos[url] = info
        self.seen_urls.add(url)
        new_urls = info.link_urls() - self.seen_urls
        self.seen_urls.update(new_urls)
        return new_urls

    def add_error(self, url: Url, msg: str) -> None:
        """
        Record a URL whose processing raised an unexpected error.

        The URL is settled exactly once, marked as a failed request and never crawled
        again.
        """

        if url in self.url_infos:
            return

        self.add_page(
            url=url,
            info=UrlInfo(result=outcome.RequestError(msg=msg), links=None),
        )

    def count(self) -> int:
        return len(self.seen_urls)

    def get_url_infos(self) -> Mapping[Url, UrlInfo]:
        return self.url_infos
