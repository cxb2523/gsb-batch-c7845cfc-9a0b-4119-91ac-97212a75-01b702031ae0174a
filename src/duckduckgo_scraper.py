from typing import List, Optional, Union

from .write_output import write_output

from .search import FAILED_DUE_TO_CREDITS_EXHAUSTED, FAILED_DUE_TO_NO_KEY,FAILED_DUE_TO_NOT_SUBSCRIBED, FAILED_DUE_TO_UNKNOWN_ERROR, search


class SearchResults(list):
    """List of scraped results, carrying a per-query settlement summary.

    It behaves exactly like the ``list`` returned by older versions of the
    library (same items, same ordering, same writing side effects) and exposes
    the batch settlement as ``.summary`` so callers can tell which queries
    succeeded, which only returned a page or two, and which failed outright.
    """

    def __init__(self, items=(), summary=None):
        super().__init__(items)
        self.summary = summary or {
            "total_queries": 0,
            "successful_count": 0,
            "partial_count": 0,
            "failed_count": 0,
            "successful": [],
            "partial": [],
            "failed": [],
            "total_results": 0,
        }

    @property
    def successful(self):
        return self.summary["successful"]

    @property
    def partial(self):
        return self.summary["partial"]

    @property
    def failed(self):
        return self.summary["failed"]

    def _keyed(self, status):
        return [item["query"] for item in self.summary[status]]

    def successful_queries(self):
        return self._keyed("successful")

    def partial_queries(self):
        return self._keyed("partial")

    def failed_queries(self):
        return self._keyed("failed")

    def to_dict(self):
        """Return the settlement summary as a plain dictionary."""
        return self.summary

    def __getitem__(self, key):
        # Convenience: allow result["summary"], while keeping list semantics
        # for integer indexes and slices intact.
        if isinstance(key, str) and key == "summary":
            return self.summary
        return super().__getitem__(key)


def clean_data(social_details):
    success, credits_exhausted, not_subscribed, unknown_error, no_key = [], [], [], [], []

    for detail in social_details:
        if detail.get("error") is None:
            success.append(detail)
        elif detail["error"] == FAILED_DUE_TO_CREDITS_EXHAUSTED:
            credits_exhausted.append(detail)
        elif detail["error"] == FAILED_DUE_TO_NOT_SUBSCRIBED:
            not_subscribed.append(detail)
        elif detail["error"] == FAILED_DUE_TO_UNKNOWN_ERROR:
            unknown_error.append(detail)
        elif detail["error"] == FAILED_DUE_TO_NO_KEY:
            no_key.append(detail)

    return success, credits_exhausted, not_subscribed, unknown_error, no_key

def print_data_errors(credits_exhausted, not_subscribed, unknown_error, no_key):

    if credits_exhausted:
        name = "queries" if len(credits_exhausted) > 1 else "query"
        print(f"Could not get data for {len(credits_exhausted)} {name} due to credit exhaustion. Please consider upgrading your plan by visiting https://rapidapi.com/Chetan11dev/api/duckduckgo-scraper/pricing to continue scraping data.")

    if not_subscribed:
        name = "queries" if len(not_subscribed) > 1 else "query"
        print(f"Could not get data for {len(not_subscribed)} {name} as you are not subscribed to DuckDuckGo Scraper API. Please subscribe to a free plan by visiting https://rapidapi.com/Chetan11dev/api/duckduckgo-scraper/pricing")

    if unknown_error:
        name = "queries" if len(unknown_error) > 1 else "query"
        print(f"Could not get data for {len(unknown_error)} {name} due to Unknown Error.")

    if no_key:
        name = "queries" if len(no_key) > 1 else "query"
        print(f"Could not get data for {len(no_key)} {name} as you are not subscribed to DuckDuckGo Scraper API. Please subscribe to a free plan by visiting https://rapidapi.com/Chetan11dev/api/duckduckgo-scraper/pricing")


def _print_partial_errors(partial):
    for item in partial:
        print(
            f"Partial data for query '{item['query']}' ({item['count']} results) "
            f"because pagination failed due to {item['error']}."
        )


def _print_summary(summary):
    print("Search summary:")
    print(f"    successful: {summary['successful_count']}")
    for item in summary["successful"]:
        print(f"        {item['query']}: {item['count']} results")
    print(f"    partial: {summary['partial_count']}")
    for item in summary["partial"]:
        print(f"        {item['query']}: {item['count']} results ({item['error']})")
    print(f"    failed: {summary['failed_count']}")
    for item in summary["failed"]:
        print(f"        {item['query']}: {item['error']}")


class DuckDuckGo :

    @staticmethod
    def search(query:  Union[str, List[str]],  max: Optional[int] = None, key: Optional[str] =None,  use_cache: bool = True) -> SearchResults:
        """
        Function to scrape data from DuckDuckGo.
        :param query: A single query or a list of queries.
        :param max: Maximum number of results to keep per query.
        :param key: RapidAPI key.
        :param use_cache: Boolean indicating whether to use cached data.
        :return: A list of result dictionaries (backwards compatible with the
                 original return value) with a ``.summary`` attribute holding
                 the per-query settlement: successful/partial/failed queries,
                 result counts and error types.
        """
        cache = use_cache
        if isinstance(query, str):
            query = [query]

        queries = [{"query":query_query, "max": max} for query_query in query]
        result = []
        summary = {
            "total_queries": len(queries),
            "successful_count": 0,
            "partial_count": 0,
            "failed_count": 0,
            "successful": [],
            "partial": [],
            "failed": [],
            "total_results": 0,
        }

        for item in queries:
            query_text = item["query"]
            data = item
            metadata = {"key": key}

            try:
                result_item = search(data, cache=cache, metadata=metadata)
            except KeyboardInterrupt:
                raise
            except Exception:
                # Never let one query take down the whole batch: record it as
                # an unknown failure and keep going.
                import traceback
                traceback.print_exc()
                result_item = {
                    "data": None,
                    "error": FAILED_DUE_TO_UNKNOWN_ERROR,
                    "partial": False,
                    "results": [],
                    "count": 0,
                }

            if not isinstance(result_item, dict):
                result_item = {
                    "data": None,
                    "error": FAILED_DUE_TO_UNKNOWN_ERROR,
                    "partial": False,
                    "results": [],
                    "count": 0,
                }

            error = result_item.get("error")
            is_partial = bool(result_item.get("partial"))

            if error is None:
                payload = result_item.get("data")
                if not isinstance(payload, dict):
                    payload = {}
                results = list(payload.get("results") or [])
                result.extend(results)
                summary["successful"].append({"query": query_text, "count": len(results)})
                summary["successful_count"] += 1
                summary["total_results"] += len(results)
                write_output(query_text, results, None)
            elif is_partial:
                # Keep what was scraped before the failing page, but never
                # cache a partial payload and never report it as a success.
                results = list(result_item.get("results") or [])
                entry = {
                    "query": query_text,
                    "count": len(results),
                    "error": error,
                }
                result.extend(results)
                summary["partial"].append(entry)
                summary["partial_count"] += 1
                summary["total_results"] += len(results)
                write_output(query_text, results, None)
                _print_partial_errors([entry])
            else:
                # Total failure: no output file, error type exposed in summary.
                summary["failed"].append({"query": query_text, "error": error})
                summary["failed_count"] += 1
                print_data_errors(*_error_groups(result_item, error))

        try:
            if result:
                # bt.write_json(result, "result")
                write_output('_all',result, None, lambda x:x)

            _print_summary(summary)

            return SearchResults(result, summary)
        finally:
            search.close()


def _error_groups(result_item, error):
    """Group the current query's settlement by error type for the legacy
    per-type messages printed by :func:`print_data_errors`."""
    groups = ([], [], [], [])
    if error is None:
        return groups
    mapping = {
        FAILED_DUE_TO_CREDITS_EXHAUSTED: 0,
        FAILED_DUE_TO_NOT_SUBSCRIBED: 1,
        FAILED_DUE_TO_UNKNOWN_ERROR: 2,
        FAILED_DUE_TO_NO_KEY: 3,
    }
    bucket = mapping.get(error)
    if bucket is not None:
        groups[bucket].append(result_item)
    return groups
