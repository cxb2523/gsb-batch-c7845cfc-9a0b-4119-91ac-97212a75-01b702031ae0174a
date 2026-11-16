# CF-Clearance-Scraper
A simple program for scraping Cloudflare clearance (cf_clearance) cookies from websites issuing Cloudflare challenges to visitors. This program works on all Cloudflare challenge types (JavaScript, managed, and interactive).

## Clearance Cookie Usage
In order to bypass Cloudflare challenges with the clearance cookies, you must make sure of two things:

- The user agent used to fetch the clearance cookie must match the user agent being used within the requests that use the clearance cookie
- The IP address used to fetch the clearance cookie must match the IP address being used to make the requests that use the clearance cookie

```mermaid
flowchart
	N14e["cf_clearance"]
	N14f["IP Address"]
	N150["User Agent"]
	N14e --> N14f
	N14e --> N150
```

## Installation
Download and install [Google Chrome](https://www.google.com/chrome/index.html).

Then, install the Python dependencies:

    $ pip install -r requirements.txt

## Usage
> [!WARNING]
> Depending on the user agent used, it may affect your ability to solve the Cloudflare challenge.

```
usage: main.py [-h] [-f FILE] [-t TIMEOUT] [-p PROXY] [-ua USER_AGENT] [--disable-http2] [--disable-http3] [--headed] [-ac] [-c] [-w] [-a] [-j] URL

A simple program for scraping Cloudflare clearance (cf_clearance) cookies from websites issuing Cloudflare challenges to visitors

positional arguments:
  URL                   The URL to scrape the Cloudflare clearance cookie from

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  The file to write the Cloudflare clearance cookie information to, in JSON format
  -t TIMEOUT, --timeout TIMEOUT
                        The timeout in seconds to use for solving challenges
  -p PROXY, --proxy PROXY
                        The proxy server URL to use for the browser requests
  -ua USER_AGENT, --user-agent USER_AGENT
                        The user agent to use for the browser requests
  --disable-http2       Disable the usage of HTTP/2 for the browser requests
  --disable-http3       Disable the usage of HTTP/3 for the browser requests
  --headed              Run the browser in headed mode
  -ac, --all-cookies    Retrieve all cookies from the page, not just the Cloudflare clearance cookie
  -c, --curl            Get the cURL command for the request with the cookies and user agent
  -w, --wget            Get the Wget command for the request with the cookies and user agent
  -a, --aria2           Get the aria2 command for the request with the cookies and user agent
  -j, --json            Print the structured collection result as JSON
```

The command line entry point is a thin wrapper around `collect_clearance()` and
reports the same status, challenge type, cookies, user agent, proxy, duration
and failure reason that the programmatic interface returns.

## Programmatic interface

```python
import asyncio

from main import CollectionStatus, collect_clearance


async def main() -> None:
    result = await collect_clearance(
        "https://example.com/challenge",
        timeout=30,
        proxy="http://user:pass@proxy:8080",
    )

    if result.status is CollectionStatus.SUCCESS:
        print(result.cf_clearance["value"], result.user_agent)
    elif result.status is CollectionStatus.NO_CHALLENGE:
        print("The page did not issue a challenge")
    else:
        print(result.status.value, result.error)

    # Cookies obtained before a failure are kept.
    print(result.cookies, result.elapsed)


asyncio.run(main())
```

`collect_clearance()` never raises for browser launch failures, page request
errors, proxy authentication failures or challenge timeouts; those are reported
through the returned `CollectionResult`. The result exposes:

| field            | meaning                                                                 |
| ---------------- | ----------------------------------------------------------------------- |
| `status`         | `success`, `no_challenge`, `timeout`, `proxy_error`, `browser_error`, `page_error` or `error` |
| `challenge_type` | detected `ChallengePlatform`, if any                                     |
| `cookies`        | every cookie present on finish, including partial results after failure  |
| `cf_clearance`   | the extracted clearance cookie, if one was obtained                      |
| `user_agent`     | user agent of the browser that produced the cookies (same-origin)        |
| `proxy`          | proxy URL that was used, if any                                          |
| `elapsed`        | duration of the attempt in seconds                                       |
| `error`          | failure reason, `None` for `success` and `no_challenge`                  |

`success` and `no_challenge` are both successful outcomes (`result.ok` is true);
challenge detection and the solving loop never click after `timeout` elapses,
and a failed proxy authentication is always reported as `proxy_error` rather
than as a timeout or as "no challenge".

## Tests

The test suite stubs the browser or serves fake pages from a local HTTP server,
so it is fully local and deterministic:

    $ pip install -r requirements.txt pytest
    $ pytest

The tests cover the four acceptance paths:

- `success` - a `cf_clearance` cookie is obtained
- `no_challenge` - the page issues no challenge and no cookie
- `timeout` - the challenge is never solved; the loop stops near `timeout`
- `proxy authentication failure` - reported as `proxy_error`, not `timeout`


## Example
    $ python main.py -f cookies.json https://sergiodemo.com/security/challenge/legacy-challenge
    [14:24:27] [INFO] Launching headless browser...
    [14:24:27] [INFO] Going to https://sergiodemo.com/security/challenge/legacy-challenge...
    [14:24:28] [INFO] Solving Cloudflare challenge [Interactive]...
    [14:24:31] [INFO] Cookie: cf_clearance=SkyEdEGvKp1BBA2NpRW3Azsw5neMD6sEEqJd6jOCCfs-1736886257-1.2.1.1-cam47ywp3q_yKE1bw0lZ2YS83dnh_BsIHtS7earbsYE.AxQDBtZiifiHvp1nZGRhABaSdjU7XRQpUCVwUSrlJGH8DXr50YR18pNLxBvcEJFO2gPMxr.ZjKze8rWgM9H4rPeET67jzAo_ZRpNP6hGCvdyO62VVCtqDBQDKhKZz9yZQp7YEHK7tchQIteVgu.dUxYdan5_D.R0zewnS382BP0w1AoTf2p40.lQwbhgildEiKG14xACd13V4EEthkZV0dnliwcn35rT3h32ODf50MABQNSQ8WjhZhbLSNOPO_zEhrK9R0Yn4eBuRKvnL9_x9jKvaBPDPAgyiZv_VzFP_g
    [14:24:31] [INFO] User agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36
    [14:24:31] [INFO] Writing Cloudflare clearance cookie information to cookies.json...
