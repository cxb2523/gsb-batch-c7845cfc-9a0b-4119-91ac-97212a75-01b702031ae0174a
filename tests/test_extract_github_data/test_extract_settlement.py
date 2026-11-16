import importlib
import json
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from extract_core import extract as extract_module
from extract_core.extract import (
    add_language_failures_to_settlement,
    fetch_language_data,
    fetch_repos_per_topic,
    get_all_repos_data,
    get_languages_data,
)


RUN_DATE = datetime(2024, 5, 6)
RUN_DATE_PATH = "2024/05/06"


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return RUN_DATE


def make_repo(repo_id, name, topic):
    return {
        "id": repo_id,
        "name": name,
        "languages_url": f"https://api.github.com/repos/{name}/languages",
        "size": 10,
        "stargazers_count": 1,
        "language": "Python",
        "forks_count": 1,
        "license": {"spdx_id": "MIT"},
        "open_issues_count": 0,
        "topics": [topic],
        "topic_queried": topic
    }


def make_settings(pages_per_topic=3, languages_per_topic=50):
    return SimpleNamespace(
        github_api_token="test-token",
        results_per_page=100,
        pages_per_topic=pages_per_topic,
        languages_per_topic=languages_per_topic
    )


def make_page(items, total_count=123):
    return {"items": items, "total_count": total_count}


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(extract_module.time, "sleep", lambda *_: None)


def test_fetch_repos_per_topic_keeps_first_page_when_second_page_fails(monkeypatch, no_sleep):
    requested_pages = []

    def fake_get_repos_from_page(topic, page, settings):
        requested_pages.append(page)
        if page == 2:
            raise Exception("Max retries (5) reached for page 2!")
        return make_page([make_repo(page, f"repo-{page}", topic)], total_count=99)

    monkeypatch.setattr(extract_module, "get_repos_from_page", fake_get_repos_from_page)

    repo_data, repo_counts, failure = fetch_repos_per_topic("topic_a", make_settings())

    assert requested_pages == [1, 2], "must stop fetching pages after the failed page"
    assert [repo["id"] for repo in repo_data] == [1]
    assert repo_counts == 99
    assert failure == {"topic": "topic_a", "page": 2, "error": "Max retries (5) reached for page 2!"}


def test_fetch_repos_per_topic_has_no_failure_when_all_pages_succeed(monkeypatch, no_sleep):
    def fake_get_repos_from_page(topic, page, settings):
        return make_page([make_repo(page, f"repo-{page}", topic)], total_count=3)

    monkeypatch.setattr(extract_module, "get_repos_from_page", fake_get_repos_from_page)

    repo_data, repo_counts, failure = fetch_repos_per_topic("topic_a", make_settings())

    assert [repo["id"] for repo in repo_data] == [1, 2, 3]
    assert repo_counts == 3
    assert failure is None


def test_get_all_repos_data_continues_with_next_topic_after_failure(monkeypatch, no_sleep):
    def fake_get_repos_from_page(topic, page, settings):
        if topic == "topic_a" and page == 2:
            raise Exception("page 2 exploded")
        repo = make_repo(repo_id_for(topic, page), f"{topic}-repo-{page}", topic)
        return make_page([repo], total_count=10)

    monkeypatch.setattr(extract_module, "get_repos_from_page", fake_get_repos_from_page)

    repo_data, repo_counts, settlement = get_all_repos_data(["topic_a", "topic_b"], make_settings())

    # page 1 of topic_a is kept even though page 2 failed
    assert [(repo["topic_queried"], repo["id"]) for repo in repo_data] == [
        ("topic_a", 101),
        ("topic_b", 201),
        ("topic_b", 202),
        ("topic_b", 203)
    ]
    assert repo_counts == {"topic_a": 10, "topic_b": 10}

    assert settlement["topics_total"] == 2
    assert settlement["topics_succeeded"] == 1
    assert settlement["topics_failed"] == 1
    assert settlement["language_failed_repos"] == 0

    topic_a = settlement["topics"]["topic_a"]
    assert topic_a["success"] is False
    assert topic_a["pages_fetched"] == 1
    assert topic_a["repos_fetched"] == 1
    assert topic_a["failed_pages"] == [
        {"topic": "topic_a", "page": 2, "error": "page 2 exploded"}
    ]

    topic_b = settlement["topics"]["topic_b"]
    assert topic_b["success"] is True
    assert topic_b["pages_fetched"] == 3
    assert topic_b["failed_pages"] == []


def test_fetch_language_data_skips_failed_repo(monkeypatch):
    repos = [
        make_repo(1, "good", "topic_a"),
        make_repo(2, "broken", "topic_a"),
        make_repo(3, "also-good", "topic_b"),
    ]

    def fake_get_languages(languages_url, settings):
        if "broken" in languages_url:
            raise Exception("languages api down")
        return {"Python": 100}

    monkeypatch.setattr(extract_module, "get_languages", fake_get_languages)

    language_data, failures = fetch_language_data(repos, make_settings())

    assert [row["repo_id"] for row in language_data] == [1, 3]
    assert failures == [{
        "topic": "topic_a",
        "repo_id": 2,
        "repo_name": "broken",
        "error": "languages api down"
    }]


def repo_id_for(topic, page):
    """Builds a unique repo id per topic so deduplication cannot merge test rows."""
    return (["topic_a", "topic_b"].index(topic) + 1) * 100 + page


def test_settlement_records_language_failure_count(monkeypatch, no_sleep):
    def fake_get_repos_from_page(topic, page, settings):
        repo = make_repo(repo_id_for(topic, page), f"{topic}-repo-{page}", topic)
        return make_page([repo], total_count=2)

    def fake_get_languages(languages_url, settings):
        if "topic_b-repo-1" in languages_url:
            raise Exception("languages api down")
        return {"Python": 100}

    monkeypatch.setattr(extract_module, "get_repos_from_page", fake_get_repos_from_page)
    monkeypatch.setattr(extract_module, "get_languages", fake_get_languages)

    repo_data, _, settlement = get_all_repos_data(["topic_a", "topic_b"], make_settings())
    language_data, language_failures = get_languages_data(repo_data, make_settings())
    settlement = add_language_failures_to_settlement(settlement, language_failures)

    assert settlement["language_failed_repos"] == 1
    assert settlement["topics_failed"] == 1
    assert settlement["topics"]["topic_a"]["success"] is True
    topic_b = settlement["topics"]["topic_b"]
    assert topic_b["success"] is False
    assert topic_b["language_failed_repos"] == 1
    assert topic_b["failed_pages"] == []
    assert topic_b["language_failures"][0]["repo_id"] == 201
    # the failed repo's language rows are skipped, but it is still in the repo data
    assert 201 not in {row["repo_id"] for row in language_data}
    assert {row["repo_id"] for row in language_data} == {101, 102, 103, 202, 203}
    assert {repo["id"] for repo in repo_data} == {101, 102, 103, 201, 202, 203}


def test_settlement_has_no_failure_records_when_everything_succeeds(monkeypatch, no_sleep):
    def fake_get_repos_from_page(topic, page, settings):
        repo = make_repo(repo_id_for(topic, page), f"{topic}-repo-{page}", topic)
        return make_page([repo], total_count=3)

    def fake_get_languages(languages_url, settings):
        return {"Python": 100}

    monkeypatch.setattr(extract_module, "get_repos_from_page", fake_get_repos_from_page)
    monkeypatch.setattr(extract_module, "get_languages", fake_get_languages)

    repo_data, _, settlement = get_all_repos_data(["topic_a", "topic_b"], make_settings())
    _, language_failures = get_languages_data(repo_data, make_settings())
    settlement = add_language_failures_to_settlement(settlement, language_failures)

    assert settlement["topics_total"] == 2
    assert settlement["topics_succeeded"] == 2
    assert settlement["topics_failed"] == 0
    assert settlement["language_failed_repos"] == 0
    assert language_failures == []
    for topic_settlement in settlement["topics"].values():
        assert topic_settlement["success"] is True
        assert topic_settlement["failed_pages"] == []
        assert topic_settlement["language_failures"] == []
        assert topic_settlement["language_failed_repos"] == 0


class FakeS3Client:
    """Stores uploaded objects in memory."""

    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[Key] = Body

    def upload_fileobj(self, Bucket, Key, Fileobj):
        self.objects[Key] = Fileobj.read()


@pytest.fixture
def extract_lambda(monkeypatch):
    monkeypatch.setenv("GITHUB_API_TOKEN", "test-token")
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "test-function")
    import extract.lambda_function as lambda_function
    return importlib.reload(lambda_function)


def run_handler(monkeypatch, extract_lambda, topics, get_repos_from_page, get_languages, registry=None):
    fake_s3 = FakeS3Client()
    extract_lambda.settings.pages_per_topic = 3

    monkeypatch.setattr(extract_lambda, "create_s3_client", lambda **kwargs: fake_s3)
    monkeypatch.setattr(extract_lambda, "get_search_queries", lambda **kwargs: topics)
    monkeypatch.setattr(extract_lambda, "get_json_object", lambda *args, **kwargs: dict(registry or {}))
    monkeypatch.setattr(extract_lambda, "datetime", FixedDatetime)
    monkeypatch.setattr(extract_module, "get_repos_from_page", get_repos_from_page)
    monkeypatch.setattr(extract_module, "get_languages", get_languages)
    monkeypatch.setattr(extract_module.time, "sleep", lambda *_: None)

    extract_lambda.lambda_handler(None, None)

    return fake_s3.objects


def test_lambda_writes_all_outputs_with_partial_failure(monkeypatch, extract_lambda):
    def fake_get_repos_from_page(topic, page, settings):
        if topic == "topic_a" and page == 2:
            raise Exception("page 2 exploded")
        repo = make_repo(repo_id_for(topic, page), f"{topic}-repo-{page}", topic)
        return make_page([repo], total_count=7)

    def fake_get_languages(languages_url, settings):
        if "topic_b-repo-1" in languages_url:
            raise Exception("languages api down")
        return {"Python": 100}

    objects = run_handler(
        monkeypatch, extract_lambda, ["topic_a", "topic_b"],
        fake_get_repos_from_page, fake_get_languages,
        registry={
            # 101 was seen before: last_seen is refreshed while first_seen is kept
            "101": {"name": "topic_a-repo-1", "first_seen": "2024-01-01", "last_seen": "2024-01-01"}
        }
    )

    repos_key = f"github_data/{RUN_DATE_PATH}/repos.parquet"
    languages_key = f"github_data/{RUN_DATE_PATH}/languages.parquet"
    counts_key = f"github_data/{RUN_DATE_PATH}/repo_counts.json"
    settlement_key = f"github_data/{RUN_DATE_PATH}/settlement.json"
    assert set(objects) == {
        "reference_data/repo_registry.json",
        repos_key, languages_key, counts_key, settlement_key
    }

    repos = pq.read_table(BytesIO(objects[repos_key])).to_pylist()
    languages_long = pq.read_table(BytesIO(objects[languages_key])).to_pylist()
    counts = json.loads(objects[counts_key])
    settlement = json.loads(objects[settlement_key])
    registry = objects["reference_data/repo_registry.json"]
    if not isinstance(registry, dict):
        registry = json.loads(registry)

    # page 1 of topic_a survived, topic_b was fetched completely
    assert {(repo["topic_queried"], repo["id"]) for repo in repos} == {
        ("topic_a", 101), ("topic_b", 201), ("topic_b", 202), ("topic_b", 203)
    }
    assert counts == {"topic_a": 7, "topic_b": 7}

    # registry semantics are unchanged: refresh last_seen, keep first_seen
    assert registry["101"]["first_seen"] == "2024-01-01"
    assert registry["101"]["last_seen"] == RUN_DATE.strftime("%Y-%m-%d")
    assert registry["201"]["first_seen"] == RUN_DATE.strftime("%Y-%m-%d")
    assert registry["201"]["last_seen"] == RUN_DATE.strftime("%Y-%m-%d")

    # the repo whose language request failed has no language rows but is in repos + registry
    assert 201 not in {row["repo_id"] for row in languages_long}
    assert {row["repo_id"] for row in languages_long} == {101, 202, 203}
    failed_language_repo = next(repo for repo in repos if repo["name"] == "topic_b-repo-1")
    assert failed_language_repo["id"] == 201
    assert failed_language_repo["topic_queried"] == "topic_b"

    assert settlement["topics_total"] == 2
    assert settlement["topics_succeeded"] == 0
    assert settlement["topics_failed"] == 2
    assert settlement["language_failed_repos"] == 1
    assert settlement["repo_counts"] == counts
    assert settlement["topics"]["topic_a"]["failed_pages"] == [
        {"topic": "topic_a", "page": 2, "error": "page 2 exploded"}
    ]
    assert settlement["topics"]["topic_b"]["failed_pages"] == []
    assert settlement["topics"]["topic_b"]["language_failed_repos"] == 1


def test_lambda_settlement_has_no_failures_when_all_succeeds(monkeypatch, extract_lambda):
    def fake_get_repos_from_page(topic, page, settings):
        repo = make_repo(repo_id_for(topic, page), f"{topic}-repo-{page}", topic)
        return make_page([repo], total_count=3)

    def fake_get_languages(languages_url, settings):
        return {"Python": 100}

    objects = run_handler(
        monkeypatch, extract_lambda, ["topic_a", "topic_b"],
        fake_get_repos_from_page, fake_get_languages
    )

    settlement = json.loads(objects[f"github_data/{RUN_DATE_PATH}/settlement.json"])
    counts = json.loads(objects[f"github_data/{RUN_DATE_PATH}/repo_counts.json"])

    assert counts == {"topic_a": 3, "topic_b": 3}
    assert settlement["topics_total"] == 2
    assert settlement["topics_succeeded"] == 2
    assert settlement["topics_failed"] == 0
    assert settlement["language_failed_repos"] == 0
    for topic in ["topic_a", "topic_b"]:
        assert settlement["topics"][topic]["success"] is True
        assert settlement["topics"][topic]["failed_pages"] == []
        assert settlement["topics"][topic]["language_failures"] == []


def test_lambda_keeps_other_topics_when_first_page_fails(monkeypatch, extract_lambda):
    def fake_get_repos_from_page(topic, page, settings):
        if topic == "topic_a":
            raise Exception("topic_a first page exploded")
        repo = make_repo(repo_id_for(topic, page), f"{topic}-repo-{page}", topic)
        return make_page([repo], total_count=3)

    def fake_get_languages(languages_url, settings):
        return {"Python": 100}

    objects = run_handler(
        monkeypatch, extract_lambda, ["topic_a", "topic_b"],
        fake_get_repos_from_page, fake_get_languages
    )

    repos = pq.read_table(BytesIO(objects[f"github_data/{RUN_DATE_PATH}/repos.parquet"])).to_pylist()
    counts = json.loads(objects[f"github_data/{RUN_DATE_PATH}/repo_counts.json"])
    settlement = json.loads(objects[f"github_data/{RUN_DATE_PATH}/settlement.json"])
    registry = objects["reference_data/repo_registry.json"]
    if not isinstance(registry, dict):
        registry = json.loads(registry)

    assert {repo["topic_queried"] for repo in repos} == {"topic_b"}
    assert counts == {"topic_b": 3}
    assert set(registry) == {str(repo["id"]) for repo in repos}
    assert settlement["topics_succeeded"] == 1
    topic_a = settlement["topics"]["topic_a"]
    assert topic_a["success"] is False
    assert topic_a["pages_fetched"] == 0
    assert topic_a["repo_count"] is None
    assert topic_a["failed_pages"] == [
        {"topic": "topic_a", "page": 1, "error": "topic_a first page exploded"}
    ]


def test_lambda_still_writes_readable_outputs_when_every_topic_fails(monkeypatch, extract_lambda):
    def fake_get_repos_from_page(topic, page, settings):
        raise Exception(f"{topic} page {page} exploded")

    def fake_get_languages(languages_url, settings):
        raise Exception("languages api down")

    objects = run_handler(
        monkeypatch, extract_lambda, ["topic_a", "topic_b"],
        fake_get_repos_from_page, fake_get_languages
    )

    repos_key = f"github_data/{RUN_DATE_PATH}/repos.parquet"
    languages_key = f"github_data/{RUN_DATE_PATH}/languages.parquet"
    counts_key = f"github_data/{RUN_DATE_PATH}/repo_counts.json"
    settlement_key = f"github_data/{RUN_DATE_PATH}/settlement.json"
    assert set(objects) == {
        "reference_data/repo_registry.json",
        repos_key, languages_key, counts_key, settlement_key
    }

    repos_table = pq.read_table(BytesIO(objects[repos_key]))
    languages_table = pq.read_table(BytesIO(objects[languages_key]))
    assert repos_table.num_rows == 0
    assert languages_table.num_rows == 0
    # explicit schemas keep the empty files readable by the aggregation lambda
    assert "id" in repos_table.column_names
    assert "language" in languages_table.column_names

    assert json.loads(objects[counts_key]) == {}
    settlement = json.loads(objects[settlement_key])
    assert settlement["topics_total"] == 2
    assert settlement["topics_failed"] == 2
    assert settlement["topics_succeeded"] == 0
    for topic in ["topic_a", "topic_b"]:
        assert settlement["topics"][topic]["failed_pages"][0]["page"] == 1
