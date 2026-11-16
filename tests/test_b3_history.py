import datetime as dt
import io
import zipfile

import pytest
import requests

from brdata.b3 import history


def _zip_bytes(filename: str = "COTAHIST.TXT", content: bytes = b"data") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        zip_file.writestr(filename, content)
    return buffer.getvalue()


def _mock_response(
    mocker,
    status_code: int = 200,
    headers: dict | None = None,
    chunks: list[bytes] | None = None,
):
    response = mocker.Mock()
    response.__enter__ = mocker.Mock(return_value=response)
    response.__exit__ = mocker.Mock(return_value=None)
    response.status_code = status_code
    response.headers = headers if headers is not None else {
        "content-type": "application/zip"
    }
    response.iter_content.return_value = (
        chunks if chunks is not None else [_zip_bytes()]
    )
    response.raise_for_status = mocker.Mock()
    return response


def _fake_get(mocker, url_to_response: dict, url_to_error: dict | None = None):
    """Patches requests.get with URL-based behavior.

    URLs listed in ``url_to_error`` raise the given exception, URLs listed in
    ``url_to_response`` return the given mock response.
    """
    url_to_error = url_to_error or {}

    def _get(url, **kwargs):
        if url in url_to_error:
            raise url_to_error[url]
        return url_to_response[url]

    return mocker.patch("brdata.b3.history.requests.get", side_effect=_get)


def test_cotahist_file_builders():
    annual = history.annual_file(2026)
    monthly = history.monthly_file(2026, 5)
    daily = history.daily_file("2026-05-15")

    assert annual.filename == "COTAHIST_A2026.ZIP"
    assert annual.txt_filename == "COTAHIST_A2026.TXT"
    assert monthly.filename == "COTAHIST_M052026.ZIP"
    assert daily.filename == "COTAHIST_D15052026.ZIP"
    assert daily.ref == "2026-05-15"


def test_iter_months_returns_closed_interval():
    months = list(history.iter_months(2025, 11, 2026, 2))

    assert months == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_iter_dates_skips_weekends_when_requested():
    dates = list(history.iter_dates("2026-05-15", "2026-05-18", weekdays_only=True))

    assert dates == [dt.date(2026, 5, 15), dt.date(2026, 5, 18)]


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


def test_download_file_writes_valid_zip(tmp_path, mocker):
    response = _mock_response(mocker)
    mock_get = mocker.patch(
        "brdata.b3.history.requests.get", return_value=response
    )

    path = history.download_file(history.annual_file(2026), output_dir=tmp_path)

    assert path == tmp_path / "COTAHIST_A2026.ZIP"
    assert history.is_zip_file(path)
    mock_get.assert_called_once()


def test_download_file_raises_missing_for_404(tmp_path, mocker):
    response = _mock_response(mocker, status_code=404, headers={})
    mocker.patch("brdata.b3.history.requests.get", return_value=response)

    with pytest.raises(history.CotahistMissingError):
        history.download_file(history.annual_file(2026), output_dir=tmp_path)

    # No partial file is left behind.
    assert list(tmp_path.iterdir()) == []


def test_download_file_raises_missing_for_html_response(tmp_path, mocker):
    response = _mock_response(
        mocker,
        headers={"content-type": "text/html; charset=utf-8"},
        chunks=[b"<html><body>not found</body></html>"],
    )
    mocker.patch("brdata.b3.history.requests.get", return_value=response)

    with pytest.raises(history.CotahistMissingError):
        history.download_file(history.annual_file(2026), output_dir=tmp_path)


def test_download_file_raises_missing_for_empty_body(tmp_path, mocker):
    response = _mock_response(mocker, chunks=[b""])
    mocker.patch("brdata.b3.history.requests.get", return_value=response)

    with pytest.raises(history.CotahistMissingError):
        history.download_file(history.annual_file(2026), output_dir=tmp_path)


def test_download_file_raises_download_error_on_request_exception(
    tmp_path, mocker
):
    error = requests.ConnectionError("network is unreachable")
    mocker.patch("brdata.b3.history.requests.get", side_effect=error)

    with pytest.raises(history.CotahistDownloadError) as exc_info:
        history.download_file(history.annual_file(2026), output_dir=tmp_path)

    assert "network is unreachable" in str(exc_info.value)
    assert exc_info.value.__cause__ is error
    assert list(tmp_path.iterdir()) == []


def test_download_file_raises_download_error_on_server_error(tmp_path, mocker):
    response = _mock_response(mocker, status_code=500)
    response.raise_for_status.side_effect = requests.HTTPError("500 server error")
    mocker.patch("brdata.b3.history.requests.get", return_value=response)

    with pytest.raises(history.CotahistDownloadError):
        history.download_file(history.annual_file(2026), output_dir=tmp_path)


def test_download_file_raises_download_error_for_invalid_zip(tmp_path, mocker):
    response = _mock_response(mocker, chunks=[b"this is definitely not a zip"])
    mocker.patch("brdata.b3.history.requests.get", return_value=response)

    with pytest.raises(history.CotahistDownloadError):
        history.download_file(history.annual_file(2026), output_dir=tmp_path)

    assert not (tmp_path / "COTAHIST_A2026.ZIP").exists()


def test_missing_and_download_errors_are_distinct_exceptions():
    assert issubclass(history.CotahistMissingError, history.CotahistError)
    assert issubclass(history.CotahistDownloadError, history.CotahistError)
    assert not issubclass(history.CotahistMissingError, history.CotahistDownloadError)
    assert not issubclass(history.CotahistDownloadError, history.CotahistMissingError)


def test_download_file_returns_cached_archive_without_request(tmp_path, mocker):
    cached = tmp_path / "COTAHIST_A2026.ZIP"
    cached.write_bytes(_zip_bytes())
    mock_get = mocker.patch("brdata.b3.history.requests.get")

    path = history.download_file(history.annual_file(2026), output_dir=tmp_path)

    assert path == cached
    mock_get.assert_not_called()


def test_download_file_skips_zip_validation_when_disabled(tmp_path, mocker):
    response = _mock_response(mocker, chunks=[b"plain bytes, not a zip"])
    mock_get = mocker.patch(
        "brdata.b3.history.requests.get", return_value=response
    )

    path = history.download_file(
        history.annual_file(2026),
        output_dir=tmp_path,
        validate_zip=False,
    )

    assert path == tmp_path / "COTAHIST_A2026.ZIP"
    assert path.read_bytes() == b"plain bytes, not a zip"
    mock_get.assert_called_once()


def test_download_file_cached_invalid_zip_is_redownloaded(tmp_path, mocker):
    cached = tmp_path / "COTAHIST_A2026.ZIP"
    cached.write_bytes(b"broken archive")
    response = _mock_response(mocker)
    mock_get = mocker.patch(
        "brdata.b3.history.requests.get", return_value=response
    )

    path = history.download_file(history.annual_file(2026), output_dir=tmp_path)

    assert path == cached
    assert history.is_zip_file(path)
    mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# download_annual_series_range
# ---------------------------------------------------------------------------


def _annual_responses(mocker, years, *, missing=(), errors=()):
    responses, failures = {}, {}
    for year in years:
        item = history.annual_file(year)
        if year in missing:
            responses[item.url] = _mock_response(
                mocker, status_code=404, headers={}
            )
        elif year in errors:
            failures[item.url] = requests.ConnectionError(f"boom {year}")
        else:
            responses[item.url] = _mock_response(mocker)
    return responses, failures


def test_annual_range_all_success_report(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    responses, _ = _annual_responses(mocker, [2024, 2025, 2026])
    mock_get = _fake_get(mocker, responses)

    results = history.download_annual_series_range(
        2024, 2026, output_dir=tmp_path, sleep=1.0, report=True
    )

    assert len(results) == 3
    assert [result.ref for result in results] == ["2024", "2025", "2026"]
    assert [result.status for result in results] == [
        history.DownloadStatus.SUCCESS,
        history.DownloadStatus.SUCCESS,
        history.DownloadStatus.SUCCESS,
    ]
    assert all(result.path is not None for result in results)
    assert all(result.error is None for result in results)
    assert mock_get.call_count == 3


def test_annual_range_missing_slot_keeps_processing(tmp_path, mocker):
    sleep_mock = mocker.patch("brdata.b3.history._sleep")
    responses, _ = _annual_responses(
        mocker, [2024, 2025, 2026], missing=(2025,)
    )
    mock_get = _fake_get(mocker, responses)

    results = history.download_annual_series_range(
        2024, 2026, output_dir=tmp_path, sleep=0.5, report=True
    )

    assert [result.ref for result in results] == ["2024", "2025", "2026"]
    assert results[0].status is history.DownloadStatus.SUCCESS
    assert results[1].status is history.DownloadStatus.MISSING
    assert results[1].path is None
    assert "404" in results[1].error
    assert results[2].status is history.DownloadStatus.SUCCESS
    assert results[2].path == tmp_path / "COTAHIST_A2026.ZIP"
    assert mock_get.call_count == 3
    sleep_mock.assert_called_with(0.5)
    assert sleep_mock.call_count == 3


def test_annual_range_request_error_keeps_processing(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    responses, failures = _annual_responses(
        mocker, [2024, 2025, 2026], errors=(2025,)
    )
    mock_get = _fake_get(mocker, responses, failures)

    results = history.download_annual_series_range(
        2024, 2026, output_dir=tmp_path, report=True
    )

    assert [result.status for result in results] == [
        history.DownloadStatus.SUCCESS,
        history.DownloadStatus.FAILED,
        history.DownloadStatus.SUCCESS,
    ]
    failed = results[1]
    assert failed.path is None
    assert "boom 2025" in failed.error
    assert failed.filename == "COTAHIST_A2025.ZIP"
    assert mock_get.call_count == 3


def test_annual_range_legacy_return_without_report(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    responses, failures = _annual_responses(
        mocker, [2024, 2025, 2026], missing=(2025,), errors=(2026,)
    )
    _fake_get(mocker, responses, failures)

    paths = history.download_annual_series_range(2024, 2026, output_dir=tmp_path)

    assert paths == [tmp_path / "COTAHIST_A2024.ZIP"]
    assert all(isinstance(path, type(tmp_path)) for path in paths)

    # Explicit report=False must behave identically.
    paths_explicit = history.download_annual_series_range(
        2024, 2026, output_dir=tmp_path, report=False
    )
    assert paths_explicit == paths


def test_annual_range_cache_hit_does_not_request(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    cached = tmp_path / "COTAHIST_A2025.ZIP"
    cached.write_bytes(_zip_bytes())
    responses, _ = _annual_responses(mocker, [2024, 2025, 2026])
    mock_get = _fake_get(mocker, responses)

    results = history.download_annual_series_range(
        2024, 2026, output_dir=tmp_path, report=True
    )

    assert [result.status for result in results] == [
        history.DownloadStatus.SUCCESS,
        history.DownloadStatus.SUCCESS,
        history.DownloadStatus.SUCCESS,
    ]
    cached_item = next(result for result in results if result.ref == "2025")
    assert cached_item.path == cached
    requested_urls = [call.args[0] for call in mock_get.call_args_list]
    assert history.annual_file(2025).url not in requested_urls
    assert mock_get.call_count == 2


# ---------------------------------------------------------------------------
# download_monthly_series_range
# ---------------------------------------------------------------------------


def test_monthly_range_report_mixes_outcomes(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    items = [
        history.monthly_file(2026, 1),
        history.monthly_file(2026, 2),
        history.monthly_file(2026, 3),
    ]
    responses = {
        items[0].url: _mock_response(mocker),
        items[1].url: _mock_response(
            mocker,
            headers={"content-type": "text/html"},
            chunks=[b"<html>error</html>"],
        ),
        items[2].url: _mock_response(mocker),
    }
    mock_get = _fake_get(mocker, responses)

    results = history.download_monthly_series_range(
        2026, 1, 2026, 3, output_dir=tmp_path, report=True
    )

    assert [result.ref for result in results] == ["2026-01", "2026-02", "2026-03"]
    assert [result.status for result in results] == [
        history.DownloadStatus.SUCCESS,
        history.DownloadStatus.MISSING,
        history.DownloadStatus.SUCCESS,
    ]
    assert results[1].path is None
    assert "HTML" in results[1].error
    assert mock_get.call_count == 3


def test_monthly_range_legacy_return_without_report(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    items = [
        history.monthly_file(2026, 1),
        history.monthly_file(2026, 2),
        history.monthly_file(2026, 3),
    ]
    responses = {
        items[0].url: _mock_response(mocker),
        items[1].url: _mock_response(mocker, status_code=404, headers={}),
        items[2].url: _mock_response(mocker),
    }
    _fake_get(mocker, responses)

    paths = history.download_monthly_series_range(
        2026, 1, 2026, 3, output_dir=tmp_path
    )

    assert paths == [
        tmp_path / "COTAHIST_M012026.ZIP",
        tmp_path / "COTAHIST_M032026.ZIP",
    ]


# ---------------------------------------------------------------------------
# download_daily_series_range
# ---------------------------------------------------------------------------


def test_daily_range_report_mixes_outcomes(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    items = [
        history.daily_file("2026-05-14"),
        history.daily_file("2026-05-15"),
        history.daily_file("2026-05-16"),
    ]
    responses = {
        items[0].url: _mock_response(mocker),
        items[2].url: _mock_response(mocker),
    }
    failures = {
        items[1].url: requests.Timeout("request timed out"),
    }
    mock_get = _fake_get(mocker, responses, failures)

    results = history.download_daily_series_range(
        "2026-05-14",
        "2026-05-16",
        output_dir=tmp_path,
        weekdays_only=False,
        report=True,
    )

    assert [result.ref for result in results] == [
        "2026-05-14",
        "2026-05-15",
        "2026-05-16",
    ]
    assert [result.status for result in results] == [
        history.DownloadStatus.SUCCESS,
        history.DownloadStatus.FAILED,
        history.DownloadStatus.SUCCESS,
    ]
    assert "request timed out" in results[1].error
    assert mock_get.call_count == 3


def test_daily_range_legacy_return_without_report(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    items = [
        history.daily_file("2026-05-14"),
        history.daily_file("2026-05-15"),
    ]
    responses = {
        items[0].url: _mock_response(mocker),
        items[1].url: _mock_response(mocker, status_code=404, headers={}),
    }
    _fake_get(mocker, responses)

    paths = history.download_daily_series_range(
        "2026-05-14",
        "2026-05-15",
        output_dir=tmp_path,
        weekdays_only=False,
    )

    assert paths == [tmp_path / "COTAHIST_D14052026.ZIP"]
    assert all(isinstance(path, type(tmp_path)) for path in paths)


def test_daily_range_cache_hit_does_not_request(tmp_path, mocker):
    mocker.patch("brdata.b3.history._sleep")
    cached = tmp_path / "COTAHIST_D15052026.ZIP"
    cached.write_bytes(_zip_bytes())
    items = [
        history.daily_file("2026-05-14"),
        history.daily_file("2026-05-15"),
    ]
    responses = {items[0].url: _mock_response(mocker)}
    mock_get = _fake_get(mocker, responses)

    results = history.download_daily_series_range(
        "2026-05-14",
        "2026-05-15",
        output_dir=tmp_path,
        weekdays_only=False,
        report=True,
    )

    assert all(result.status is history.DownloadStatus.SUCCESS for result in results)
    assert mock_get.call_count == 1
    assert mock_get.call_args.args[0] == items[0].url
