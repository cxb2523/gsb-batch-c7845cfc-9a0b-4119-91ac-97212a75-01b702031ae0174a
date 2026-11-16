"""API extractor with pagination, rate-limit retries and partial-failure handling.

When no ``pagination`` block is configured the extractor keeps its original
single-GET behaviour. With pagination it walks pages until an empty page is
returned or ``max_pages`` is reached. A page that keeps failing after the
configured retries does not discard the pages already collected: the partial
data is returned inside an :class:`APIExtractionResult` and the pipeline raises
:class:`PartialExtractionError` so the run ends with an interpretable failure
instead of a silent success.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from time import sleep

import pandas as pd
import requests


class PageFetchError(Exception):
    """Raised when a single API request cannot be completed after retries."""


class PartialExtractionError(Exception):
    """Raised when a paginated extraction ends with one or more failed pages.

    The data collected from the pages that did succeed is preserved on the
    exception so callers can inspect or persist it instead of losing it.
    """

    def __init__(self, result: "APIExtractionResult") -> None:
        self.result = result
        self.frame = result.frame
        self.failed_pages = result.failed_pages
        summary = "; ".join(
            f"page {entry['page']}: {entry['error']}" for entry in result.failed_pages
        )
        super().__init__(
            f"API extraction finished with status={result.status!r} "
            f"(stop_reason={result.stop_reason!r}, "
            f"pages_fetched={result.pages_fetched}, rows_kept={len(result.frame)}). "
            f"Failed pages -> {summary}"
        )


@dataclass
class APIExtractionResult:
    """Outcome of an API extraction, including partial outcomes."""

    frame: pd.DataFrame
    status: str  # "success" | "partial" | "failed"
    pages_fetched: list[int] = field(default_factory=list)
    failed_pages: list[dict] = field(default_factory=list)
    stop_reason: str = (
        "single_request"  # single_request | empty_page | max_pages | page_failure
    )

    @property
    def ok(self) -> bool:
        return not self.failed_pages


class APIExtractor:
    def __init__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        records_path: str | None = None,
        pagination: Mapping[str, object] | None = None,
        retry: Mapping[str, object] | None = None,
        session: object | None = None,
        sleeper=sleep,
    ) -> None:
        self.url = url
        self.params = params
        self.records_path = records_path
        self.session = session if session is not None else requests.Session()
        self._sleep = sleeper

        if pagination:
            if "page_param" not in pagination:
                raise ValueError(
                    "pagination.page_param is required when pagination is configured"
                )
            self.pagination_enabled = True
            self.page_param = str(pagination["page_param"])
            self.start_page = int(pagination.get("start_page", 1))
            self.page_size = pagination.get("page_size")
            self.page_size_param = str(pagination.get("page_size_param", "per_page"))
            self.max_pages = int(pagination.get("max_pages", 100))
            if self.max_pages < 1:
                raise ValueError("pagination.max_pages must be >= 1")
            if self.start_page < 0:
                raise ValueError("pagination.start_page must be >= 0")
        else:
            self.pagination_enabled = False

        retry = retry or {}
        self.max_retries = int(retry.get("max_retries", 0))
        self.backoff_seconds = float(retry.get("backoff_seconds", 0))
        if self.max_retries < 0:
            raise ValueError("retry.max_retries must be >= 0")

    def extract(self) -> APIExtractionResult:
        if not self.pagination_enabled:
            return self._extract_single()
        return self._extract_paginated()

    def _extract_single(self) -> APIExtractionResult:
        """Original behaviour: one GET, one DataFrame (retries still apply)."""
        payload = self._fetch_json(dict(self.params or {}))
        records = self._select_records(payload)
        return APIExtractionResult(
            frame=pd.DataFrame(records),
            status="success",
            stop_reason="single_request",
        )

    def _extract_paginated(self) -> APIExtractionResult:
        base_params = dict(self.params or {})
        frames: list[pd.DataFrame] = []
        pages_fetched: list[int] = []
        failed_pages: list[dict] = []
        stop_reason = "max_pages"

        for page in range(self.start_page, self.start_page + self.max_pages):
            request_params = dict(base_params)
            request_params[self.page_param] = page
            if self.page_size is not None:
                request_params[self.page_size_param] = self.page_size

            try:
                payload = self._fetch_json(request_params)
                records = self._select_records(payload)
            except PageFetchError as exc:
                failed_pages.append({"page": page, "error": str(exc)})
                stop_reason = "page_failure"
                break
            except (KeyError, TypeError) as exc:
                failed_pages.append(
                    {
                        "page": page,
                        "error": f"invalid records_path {self.records_path!r}: {exc}",
                    }
                )
                stop_reason = "page_failure"
                break

            if records is None or len(records) == 0:
                stop_reason = "empty_page"
                break

            frames.append(pd.DataFrame(records))
            pages_fetched.append(page)

        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        if failed_pages:
            status = "partial" if pages_fetched else "failed"
        else:
            status = "success"

        return APIExtractionResult(
            frame=frame,
            status=status,
            pages_fetched=pages_fetched,
            failed_pages=failed_pages,
            stop_reason=stop_reason,
        )

    def _fetch_json(self, params: dict) -> object:
        """Perform one page request with rate-limit / transient-error retries."""
        attempts = self.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self.session.get(self.url, params=params, timeout=30)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if not self._is_retryable(exc) or attempt == attempts:
                    break
                self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))

        raise PageFetchError(
            f"request failed after {attempts} attempt(s): {last_error}"
        ) from last_error

    @staticmethod
    def _is_retryable(exc: requests.RequestException) -> bool:
        """Connection/timeout errors and HTTP 429/5xx are retried; other 4xx are not."""
        if isinstance(exc, requests.HTTPError):
            response = exc.response
            status = getattr(response, "status_code", None)
            return status == 429 or (status is not None and 500 <= status < 600)
        return True

    def _select_records(self, payload: object) -> object:
        """Apply records_path extraction; logic runs independently on every page."""
        if self.records_path:
            for key in self.records_path.split("."):
                payload = payload[key]
        return payload
