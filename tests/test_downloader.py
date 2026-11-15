import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import downloader


class FakeCrawler:
    """A crawler stand-in with configurable behavior.

    behavior: "ok" (writes `yield_num` images), "empty" (writes nothing),
    or "raise" (raises on crawl).
    """

    calls = []

    def __init__(self, storage=None, behavior="ok", yield_num=5, name="Fake"):
        self.storage = storage or {}
        self.behavior = behavior
        self.yield_num = yield_num
        self.name = name

    def crawl(self, keyword=None, max_num=None):
        FakeCrawler.calls.append((self.name, keyword, max_num))
        if self.behavior == "raise":
            raise RuntimeError(f"{self.name} blew up")
        if self.behavior == "empty":
            return
        root = self.storage["root_dir"]
        os.makedirs(root, exist_ok=True)
        existing = len(os.listdir(root))
        for i in range(min(self.yield_num, max_num)):
            with open(os.path.join(root, f"{self.name}_{existing + i}.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff")  # minimal jpeg-ish bytes


def make_crawlers(behaviors, yield_num=5):
    """Build an ordered [(name, class), ...] list like downloader.get_crawlers()."""
    crawlers = []
    for name, behavior in behaviors:
        cls = type(
            f"{name}Crawler",
            (FakeCrawler,),
            {
                "__init__": lambda self, storage=None, _b=behavior, _n=name: FakeCrawler.__init__(
                    self, storage=storage, behavior=_b, yield_num=yield_num, name=_n
                )
            },
        )
        crawlers.append((name, cls))
    return crawlers


@pytest.fixture(autouse=True)
def reset_calls():
    FakeCrawler.calls = []
    yield


@pytest.fixture
def env(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setattr(downloader, "DATA_DIR", data_dir)
    return data_dir


def test_failing_source_does_not_stop_others(env, monkeypatch):
    monkeypatch.setattr(downloader, "CLASSES", {"car": ["car on road"]})
    crawlers = make_crawlers(
        [("Bing", "raise"), ("Google", "empty"), ("Baidu", "ok"), ("Flickr", "ok")],
        yield_num=10,
    )

    summary = downloader.download_images(img_nums=10, crawlers=crawlers)

    # Bing raised, Google added nothing, Baidu succeeded -> Flickr never called
    called = [name for name, _, _ in FakeCrawler.calls]
    assert called == ["Bing", "Google", "Baidu"]

    results = summary["car"]["queries"]["car on road"]
    assert results[0]["status"] == "failed"
    assert "blew up" in results[0]["reason"]
    assert results[1]["status"] == "failed"
    assert results[2]["status"] == "success"
    assert summary["car"]["final_count"] == 10
    assert summary["car"]["met"] is True


def test_all_sources_failing_does_not_abort_run(env, monkeypatch):
    monkeypatch.setattr(
        downloader, "CLASSES", {"car": ["q1"], "bus": ["q2"]}
    )
    crawlers = make_crawlers([("Bing", "raise"), ("Google", "raise")])

    summary = downloader.download_images(img_nums=5, crawlers=crawlers)

    # both classes were attempted despite every source raising
    assert set(summary) == {"car", "bus"}
    assert summary["car"]["met"] is False
    assert summary["bus"]["met"] is False
    for cls in ("car", "bus"):
        query = "q1" if cls == "car" else "q2"
        results = summary[cls]["queries"][query]
        assert all(r["status"] == "failed" for r in results)
        assert all("exception" in r["reason"] for r in results)


def test_quota_met_stops_remaining_sources(env, monkeypatch):
    monkeypatch.setattr(downloader, "CLASSES", {"car": ["car on road"]})
    crawlers = make_crawlers(
        [("Bing", "ok"), ("Google", "ok"), ("Baidu", "ok"), ("Flickr", "ok")],
        yield_num=10,
    )

    summary = downloader.download_images(img_nums=10, crawlers=crawlers)

    # Bing alone filled the quota; no later source was invoked
    called = [name for name, _, _ in FakeCrawler.calls]
    assert called == ["Bing"]
    results = summary["car"]["queries"]["car on road"]
    assert len(results) == 1
    assert results[0]["source"] == "Bing"
    assert results[0]["status"] == "success"


def test_satisfied_class_is_skipped_and_reported(env, monkeypatch):
    monkeypatch.setattr(
        downloader, "CLASSES", {"car": ["q1"], "bus": ["q2"]}
    )
    # pre-fill car with enough valid images
    car_dir = os.path.join(env, "car")
    os.makedirs(car_dir)
    for i in range(5):
        with open(os.path.join(car_dir, f"img{i}.jpg"), "wb") as f:
            f.write(b"x")
    # a non-image file must not count toward the quota
    with open(os.path.join(car_dir, "notes.txt"), "w") as f:
        f.write("not an image")

    crawlers = make_crawlers([("Bing", "ok")], yield_num=5)
    summary = downloader.download_images(img_nums=5, crawlers=crawlers)

    # car was skipped entirely: no crawler ran for its query
    queried = [kw for _, kw, _ in FakeCrawler.calls]
    assert "q1" not in queried
    assert "q2" in queried  # bus still downloaded

    car = summary["car"]
    assert car["skipped"] is True
    assert car["met"] is True
    assert car["final_count"] == 5
    assert car["queries"] == {}


def test_no_growth_counts_as_failure_and_falls_through(env, monkeypatch):
    monkeypatch.setattr(downloader, "CLASSES", {"car": ["q1"]})
    crawlers = make_crawlers([("Bing", "empty"), ("Google", "ok")], yield_num=3)

    summary = downloader.download_images(img_nums=3, crawlers=crawlers)

    results = summary["car"]["queries"]["q1"]
    assert results[0]["source"] == "Bing"
    assert results[0]["status"] == "failed"
    assert "no new" in results[0]["reason"]
    assert results[1]["source"] == "Google"
    assert results[1]["status"] == "success"
