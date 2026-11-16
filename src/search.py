
from botasaurus import bt
from botasaurus.cache import DontCache
from botasaurus import cl

try:  # botasaurus >= 4.0.90
    from botasaurus.request import request
except ImportError:  # older releases exported it from the package root
    from botasaurus import request

from time import sleep
from .utils import   default_request_options
import requests

FAILED_DUE_TO_CREDITS_EXHAUSTED = "FAILED_DUE_TO_CREDITS_EXHAUSTED"
FAILED_DUE_TO_NOT_SUBSCRIBED = "FAILED_DUE_TO_NOT_SUBSCRIBED"
FAILED_DUE_TO_NO_KEY = "FAILED_DUE_TO_NO_KEY"
FAILED_DUE_TO_UNKNOWN_ERROR = "FAILED_DUE_TO_UNKNOWN_ERROR"

def _local_storage():
    storage = getattr(bt, "LocalStorage", None)
    if storage is not None:
        return storage
    # Newer botasaurus releases dropped the `bt.LocalStorage` re-export.
    from botasaurus.local_storage import LocalStorage
    return LocalStorage


def update_credits():
    storage = _local_storage()
    credits_used  = storage.get_item("credits_used", 0)
    storage.set_item("credits_used", credits_used + 1)

def do_request(data, retry_count=3):

    params = data["params"]


    link = params["link"]
    key = data["key"]
    # print(params)
    # print("link", link)
    if retry_count == 0:
        print(f"Failed to get data, after 3 retries")
        return {
                        "data":  None,
                        "error":FAILED_DUE_TO_UNKNOWN_ERROR,
                    }



    headers = {
        "X-RapidAPI-Key": key,
    	"X-RapidAPI-Host": "duckduckgo-scraper.p.rapidapi.com"
    }


    response = requests.get(link, headers=headers)
    response_data = response.json()
    if response.status_code == 200 or response.status_code == 404:

        message = response_data.get("message", "")
        if "API doesn't exists" in message:
            return {
                        "data":  None,
                        "error":FAILED_DUE_TO_UNKNOWN_ERROR
                    }

        update_credits()
        # print(response_data)
        # bt.write_json(response_data, "response.json")
        if response.status_code  == 404:
            print(f"No data found")

            return {
                "data": response_data,
                "error": None
            }

        return {
            "data": response_data,
            "error": None
        }
    else:

        message = response_data.get("message", "")

        if "exceeded the MONTHLY quota" in message:
            return  {
                        "data":  None,
                        "error":FAILED_DUE_TO_CREDITS_EXHAUSTED
                    }
        elif "exceeded the rate limit per second for your plan" in message or "many requests" in message:
            sleep(2)
            return do_request(data, retry_count - 1)
        elif "You are not subscribed to this API." in message:
            return {
                        "data": None,
                        "error": FAILED_DUE_TO_NOT_SUBSCRIBED
                    }

        print(f"Error: {response.status_code}", response_data)

        return  {
                        "data":  None,
                        "error":FAILED_DUE_TO_UNKNOWN_ERROR,
                    }


def _settlement(error, partial, results=None):
    """Build the result payload for a query that did not fully succeed.

    ``partial`` is True when at least the first page was fetched and a later
    page failed: the already scraped results are kept so callers do not lose
    them. Such payloads are wrapped in ``DontCache`` by :func:`search`, so a
    failed/partial run can never poison the cache.
    """
    results = list(results or [])
    return {
        "data": None,
        "error": error,
        # Only a run that actually kept some results counts as partial.
        "partial": bool(partial and results),
        "results": results,
        "count": len(results),
    }


@request(**default_request_options)
def search(_, data, metadata):
    if not metadata.get('key'):
         return  DontCache(_settlement(FAILED_DUE_TO_NO_KEY, partial=False))
    max_items = data['max']
    url = "https://duckduckgo-scraper.p.rapidapi.com/search/"
    qp = {"query": data['query']}
    params = {**qp, 'link':cl.join_link(url, query_params=qp)}

    request_data = {**metadata, "params": params}
    result = do_request(request_data)

    if cl.select(result, 'error'):
        # Nothing was scraped for this query, so it is a total failure.
        return DontCache(_settlement(cl.select(result, 'error'), partial=False))

    initial_results = list(cl.select(result, 'data', 'results', default=[]) or [])
    print(f"Got {len(initial_results)} more results")

    while cl.select(result, 'data', 'next') and (max_items is None or len(initial_results) < max_items):
        next_link = cl.select(result, 'data', 'next')

        params = {**qp, 'link':next_link}
        request_data = {**metadata, "params": params}
        result = do_request(request_data)
        page_error = cl.select(result, 'error')
        if page_error:
            # A later page failed: keep the results gathered so far instead of
            # discarding the whole query. Reported as a partial success.
            if max_items is not None:
                initial_results = initial_results[:max_items]
            return DontCache(_settlement(page_error, partial=True, results=initial_results))
        more_results = list(cl.select(result, 'data', 'results', default=[]) or [])
        print(f"Got {len(more_results)} more results")
        initial_results.extend(more_results)


    if max_items is not None:
        initial_results = initial_results[:max_items]

    if not isinstance(result, dict):
        result = {}
    data_payload = result.get('data')
    if not isinstance(data_payload, dict):
        data_payload = {}
    data_payload['results'] = initial_results
    result['data'] = data_payload
    result['error'] = None
    return result
