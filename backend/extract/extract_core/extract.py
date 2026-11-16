import time
import logging
from collections import defaultdict
from urllib import parse

from extract_core.utils import make_get_request, get_scaled_delay


def get_headers(token: str) -> dict:
    return {
        "User-Agent": "data-tech-stats",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}"
    }

def parse_repo_data(data: list[dict], topic_queried: str) -> list[dict]:
    """Parses repo data and keeps and renames specific keys."""
    parsed_data = []
    for item in data:
        repo_license = item.get("license", {})
        parsed_data.append(
            {
                "id": item["id"],
                "name": item["name"],
                "languages_url": item["languages_url"],
                "size": item["size"],
                "stars": item["stargazers_count"],
                "main_language": item["language"],
                "forks": item["forks_count"],
                "license": repo_license.get("spdx_id", None) if repo_license else "",
                "open_issues": item["open_issues_count"],
                "topics": item["topics"],
                "topic_queried": topic_queried
            }
        )
    return parsed_data


def get_repos_from_page(topic: str, page: int, settings) -> dict:
    """Builds an API url and makes a request. Returns the data from the API as a dict."""
    params = f"repositories?q=topic:{topic}&page={page}&sort=stars&per_page={settings.results_per_page}"
    url = parse.urljoin(base="https://api.github.com/search/", url=params)
    logging.debug(f"Getting data from '{url}'.")
    
    response = make_get_request(url=url, headers=get_headers(settings.github_api_token))
        
    return response.json()


def get_languages(languages_url: str, settings) -> dict[str, int]:
    """Fetches languages data from the repo languages API and returns the result as a dict."""
    logging.debug(f"Fetching languages from '{languages_url}'.")
    response = make_get_request(url=languages_url, headers=get_headers(settings.github_api_token))

    return response.json()


def fetch_repos_per_topic(topic: str, settings) -> tuple[list[dict], int | None, dict | None]:
    """Fetches total repo counts and repo data for all pages for a topic.

    If a page request fails after its retries are exhausted (or its response
    cannot be parsed), the pages fetched so far are kept, fetching stops for
    this topic and the failure is returned as {"topic", "page", "error"}.
    Otherwise the failure is None.
    """
    topic_repo_data = []
    repo_counts = None
    failure = None
    for page in range(1, settings.pages_per_topic+1):
        try:
            repos_page_data = get_repos_from_page(topic, page, settings)
            parsed_page_data = parse_repo_data(repos_page_data["items"], topic)
            page_repo_counts = repos_page_data["total_count"] if page == 1 else None
        except Exception as exc:
            failure = {"topic": topic, "page": page, "error": str(exc)}
            logging.error(
                f"Failed to fetch page {page} for topic '{topic}': {exc}. "
                f"Keeping the {len(topic_repo_data)} repos already fetched for this topic."
            )
            break

        topic_repo_data.extend(parsed_page_data)
        if page_repo_counts is not None:
            repo_counts = page_repo_counts

        if page != settings.pages_per_topic: # Don't sleep after last page
            time.sleep(get_scaled_delay(settings.results_per_page))

    if failure is None:
        logging.info(f"Scraped repo data from {settings.pages_per_topic} pages for topic '{topic}'.")
    return topic_repo_data, repo_counts, failure


def fetch_language_data(repo_data: list[dict], settings) -> tuple[list[dict], list[dict]]:
    """Fetches language data for each repo on the first page of repos for each topic.

    A repo whose language request keeps failing is skipped from the language
    rows, but the failure is returned so it can be reported. The repo itself
    stays in the repo data and repo registry.
    """
    total = len(repo_data)
    processed = 0
    log_every = max(1, total // 10)

    language_data = []
    failures = []
    for repo in repo_data:
        try:
            repo_languages = get_languages(repo["languages_url"], settings)
        except Exception as exc:
            failures.append({
                "topic": repo["topic_queried"],
                "repo_id": repo["id"],
                "repo_name": repo["name"],
                "error": str(exc)
            })
            logging.error(
                f"Failed to fetch languages for repo '{repo['name']}' ({repo['id']}) "
                f"of topic '{repo['topic_queried']}': {exc}. Skipping its language rows."
            )
        else:
            language_data.append({
                "repo_id": repo["id"],
                "repo_name": repo["name"],
                "languages": repo_languages
            })
        processed += 1
        if processed % log_every == 0 or processed == total:
            logging.info(
                f"Finished fetching language data for {processed}/{total} repos "
                f"({len(failures)} failed)."
            )
    return language_data, failures


def deduplicate_repo_data(repo_data: list[dict]) -> list[dict]:
    """Deduplicates repo data by keeping the first occurance of a repo"""
    seen_ids = set()
    deduplicated = []
    for repo in repo_data:
        if repo["id"] not in seen_ids:
            seen_ids.add(repo["id"])
            deduplicated.append(repo)
    return deduplicated


def keep_n_repos_per_topic(repo_data: list[dict], n: int = 50) -> list[dict]:
    """Keeps n repos for each topic"""
    topic_counts = defaultdict(int)
    n_repos = []
    for repo in repo_data:
        if topic_counts[repo["topic_queried"]] == n:
            continue
        n_repos.append(repo)
        topic_counts[repo["topic_queried"]] += 1
    return n_repos


def get_languages_data(repo_data: list[dict], settings) -> tuple[list[dict], list[dict]]:
    """Gets deduplicated languages data for the first {languages_per_topic} repos for each topic.

    Also returns the repos whose language request failed so the run settlement
    can report them.
    """
    language_repos = keep_n_repos_per_topic(repo_data, settings.languages_per_topic)

    # Deduplicate languages after getting first N to avoid skewed data
    language_repos = deduplicate_repo_data(language_repos)
    language_data, language_failures = fetch_language_data(language_repos, settings)
    return language_data, language_failures


def get_all_repos_data(topics: list[str], settings) -> tuple[list[dict], dict, dict]:
    """Gets total repo counts and repo data per topic from all pages.

    Page failures are settled per topic: already fetched pages are kept and the
    next topic is still processed. Returns (repos_data, repo_counts, settlement)
    where settlement["topics"][topic] carries the per-topic outcome.
    """
    repo_counts = {}
    repos_data = []
    topic_settlements = {}

    for topic in topics:
        topic_repo_data, topic_repo_counts, failure = fetch_repos_per_topic(topic, settings)
        repos_data.extend(topic_repo_data)

        topic_settlement = {
            "success": failure is None,
            "pages_success": failure is None,
            "language_success": True,
            "pages_requested": settings.pages_per_topic,
            "pages_fetched": settings.pages_per_topic if failure is None else failure["page"] - 1,
            "repos_fetched": len(topic_repo_data),
            "repo_count": topic_repo_counts,
            "failed_pages": [failure] if failure else [],
            "language_failed_repos": 0,
            "language_failures": []
        }

        if topic_repo_counts is not None:
            repo_counts[topic] = topic_repo_counts
        else:
            logging.warning(
                f"No repo count available for topic '{topic}' because its first page failed."
            )

        topic_settlements[topic] = topic_settlement

    settlement = build_settlement(topic_settlements)
    return repos_data, repo_counts, settlement


def build_settlement(topic_settlements: dict[str, dict]) -> dict:
    """Builds the run settlement from per-topic outcomes.

    ``success`` is True only when every page and every language request for the
    topic succeeded. ``pages_success`` and ``language_success`` expose the two
    halves separately so a topic can be reported as partially complete.
    """
    topics = list(topic_settlements.values())
    for topic_settlement in topics:
        topic_settlement["language_failed_repos"] = len(topic_settlement["language_failures"])
        topic_settlement["pages_success"] = not topic_settlement["failed_pages"]
        topic_settlement["language_success"] = not topic_settlement["language_failures"]
        topic_settlement["success"] = (
            topic_settlement["pages_success"] and topic_settlement["language_success"]
        )

    return {
        "topics_total": len(topics),
        "topics_succeeded": sum(1 for t in topics if t["success"]),
        "topics_failed": sum(1 for t in topics if not t["success"]),
        "topics_with_page_failures": sum(1 for t in topics if not t["pages_success"]),
        "language_failed_repos": sum(t["language_failed_repos"] for t in topics),
        "topics": topic_settlements
    }


def add_language_failures_to_settlement(settlement: dict, language_failures: list[dict]) -> dict:
    """Records language failures in the settlement of their topic.

    Language failures are attributed to the topic of the affected repo; the
    per-topic success flags and the run totals are then rebuilt.
    """
    for failure in language_failures:
        topic_settlement = settlement["topics"].get(failure["topic"])
        if topic_settlement is None:
            logging.warning(
                f"Language failure for repo '{failure['repo_name']}' refers to unknown "
                f"topic '{failure['topic']}'. Skipping it in the settlement."
            )
            continue
        topic_settlement["language_failures"].append(failure)

    return build_settlement(settlement["topics"])


def build_settlement_document(repo_counts: dict, settlement: dict) -> dict:
    """Combines the run counts and the per-topic settlement for the S3 artifact."""
    return {**settlement, "repo_counts": repo_counts}
