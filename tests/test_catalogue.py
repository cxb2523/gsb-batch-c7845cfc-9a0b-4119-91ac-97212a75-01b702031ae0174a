"""The TMS quarter catalogue gates every quarter, so it must fail loudly."""

import argparse
import cProfile
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

TERMS = {
    "202615": "Fall Quarter 26-27",
    "202625": "Winter Quarter 26-27",
    "202635": "Spring Quarter 26-27",
}
CATALOGUE_HTML = "".join(
    f'<a href="/webtms_du/collegesSubjects/{code}">{label}</a>'
    for code, label in TERMS.items()
)


@pytest.fixture
def run_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DREXEL_EMAIL", "test@example.test")
    monkeypatch.setenv("DREXEL_PASSWORD", "test")
    monkeypatch.setenv("SCRAPER_FAILURE_DIR", str(tmp_path / "failures"))
    import main

    monkeypatch.setattr(main, "authenticated_session", lambda: object())
    monkeypatch.setattr(
        main, "send_request", lambda *args: SimpleNamespace(text=CATALOGUE_HTML)
    )
    monkeypatch.setattr(main.db, "initialize", lambda *args: None)
    catalogues = []
    monkeypatch.setattr(
        main.db,
        "update_catalogue",
        lambda catalogue, *args: catalogues.append(catalogue),
    )
    args = argparse.Namespace(
        db=True,
        migrate_only=False,
        all_quarters=True,
        term=None,
        ratings=True,
        all_colleges=True,
        no_file=False,
        output_file=str(tmp_path / "courses.json"),
    )
    return main, args, catalogues


def record_scrape(monkeypatch, main, **behaviour):
    scraped = []

    def scrape(**kwargs):
        scraped.append(kwargs["term_code"])
        return behaviour.get("data", {"12345": {"crn": 12345}})

    monkeypatch.setattr(main, "scrape", scrape)
    return scraped


def test_catalogue_request_failure_stops_run(run_context, monkeypatch, capsys):
    main, args, catalogues = run_context

    def fail(*args):
        raise ConnectionError("sensitive-upstream-body")

    monkeypatch.setattr(main, "send_request", fail)
    scraped = record_scrape(monkeypatch, main)
    with pytest.raises(main.CatalogueError, match="catalogue") as raised:
        main.main(args)
    assert scraped == []
    assert catalogues == []
    assert "sensitive" not in str(raised.value)
    assert "sensitive" not in capsys.readouterr().out
    diagnostic = main.format_failure(raised.value)
    assert "CatalogueError" in diagnostic
    assert "ConnectionError" in diagnostic
    assert "sensitive" not in diagnostic


def test_catalogue_unparseable_stops_run(run_context, monkeypatch):
    main, args, catalogues = run_context
    monkeypatch.setattr(
        main, "send_request", lambda *args: SimpleNamespace(text="Sign in")
    )
    scraped = record_scrape(monkeypatch, main)
    with pytest.raises(main.CatalogueError, match="catalogue"):
        main.main(args)
    assert scraped == []
    assert catalogues == []


def test_catalogue_empty_mapping_stops_run(run_context, monkeypatch):
    main, args, catalogues = run_context
    monkeypatch.setattr(main, "discover_terms", lambda html: {})
    scraped = record_scrape(monkeypatch, main)
    with pytest.raises(main.CatalogueError, match="empty"):
        main.main(args)
    assert scraped == []
    assert catalogues == []


def test_term_missing_from_catalogue_is_rejected_readably(run_context, monkeypatch):
    main, args, _ = run_context
    args.all_quarters = False
    args.term = "202645"
    scraped = record_scrape(monkeypatch, main)
    with pytest.raises(ValueError) as raised:
        main.main(args)
    message = str(raised.value)
    assert "202645" in message
    for code, label in TERMS.items():
        assert code in message and label in message
    assert scraped == []


def test_term_in_catalogue_scrapes_normally(run_context, monkeypatch):
    main, args, catalogues = run_context
    args.all_quarters = False
    args.term = "202625"
    scraped = record_scrape(monkeypatch, main)
    published = []
    monkeypatch.setattr(
        main.db, "populate_db", lambda data, code: published.append(code)
    )
    main.main(args)
    assert scraped == published == ["202625"]
    assert catalogues == [TERMS]
    assert json.loads(Path(args.output_file).read_text()) == {
        "12345": {"crn": 12345}
    }


def test_all_quarters_partial_failure_continues_and_keeps_old_data(
    run_context, monkeypatch, tmp_path
):
    main, args, catalogues = run_context
    failing = "202625"
    old_file = tmp_path / f"courses-{failing}.json"
    old_file.write_text('{"old": true}')
    published = []

    def scrape(**kwargs):
        code = kwargs["term_code"]
        if code == failing:
            raise TimeoutError("sensitive-upstream-response")
        return {"12345": {"crn": 12345, "term": code}}

    monkeypatch.setattr(main, "scrape", scrape)
    monkeypatch.setattr(
        main.db, "populate_db", lambda data, code: published.append(code)
    )
    with pytest.raises(RuntimeError, match=f"Quarters failed: {failing}"):
        main.main(args)
    assert catalogues == [TERMS]
    for code in ("202615", "202635"):
        data = json.loads((tmp_path / f"courses-{code}.json").read_text())
        assert data["12345"]["term"] == code
    assert published == ["202615", "202635"]
    # The failed quarter keeps its previously published data.
    assert json.loads(old_file.read_text()) == {"old": True}
    records = list((tmp_path / "failures").glob("failure-*.log"))
    assert len(records) == 1
    content = records[0].read_text()
    assert f"Quarter {failing}:" in content
    assert "sensitive" not in content


def test_cli_catalogue_failure_exits_nonzero_and_archives(
    run_context, monkeypatch, tmp_path
):
    main, args, _ = run_context

    def fail(*args):
        raise ConnectionError("sensitive-upstream-body")

    monkeypatch.setattr(main, "send_request", fail)

    def run_profile(*unused):
        main.main(args)

    monkeypatch.setattr(cProfile, "run", run_profile)
    monkeypatch.setattr(sys, "argv", ["main.py", "--all-quarters"])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_status:
        runpy.run_path(str(Path(main.__file__)), run_name="__main__")
    assert exit_status.value.code == 1
    records = list((tmp_path / "failures").glob("failure-*.log"))
    assert len(records) == 1
    content = records[0].read_text()
    assert "CatalogueError" in content
    assert "sensitive" not in content
