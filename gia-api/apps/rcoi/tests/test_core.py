import datetime as dt
from pathlib import Path

import pytest
from ddf import G

from apps.rcoi import models

pytestmark = pytest.mark.django_db


def test_send_subscriptions(mailoutbox):
    """
    Test - Send Subscriptions
    """
    employee = "test username"
    emp = G(models.Employee, name=employee)
    user = G(models.User)
    G(
        models.Exam,
        employee=emp,
    )
    G(
        models.Subscription,
        user=user,
        employee=emp,
    )

    models.send_subscriptions()

    assert len(mailoutbox) == 1
    assert user.email in mailoutbox[0].to
    assert emp.name in mailoutbox[0].body


def test_send_subscriptions_when_already_sent(mailoutbox):
    """
    Test - Send Subscriptions when already sent
    """
    employee = "test username"
    emp = G(models.Employee, name=employee)
    user = G(models.User)
    G(
        models.Exam,
        employee=emp,
    )
    G(
        models.Subscription,
        user=user,
        employee=emp,
        last_send=dt.datetime(2035, 1, 1),
    )

    models.send_subscriptions()
    assert len(mailoutbox) == 0


def test_send_subscriptions_when_no_subs(mailoutbox):
    """
    Test - Send Subscriptions when no subs
    """
    models.send_subscriptions()
    assert len(mailoutbox) == 0


def test_rcoi_updater(mocker_xlsx_to_csv):
    """
    Test - DB Updater
    """
    G(models.DataSource)

    # Create an "updated" datafile with "old" exam for cleanup method (must delete exam)
    updated_datafile = G(models.DataFile)
    old_exam_in_updated_datafile = G(
        models.Exam,
        datafile=updated_datafile,
        created=dt.datetime(2019, 1, 1),
        modified=dt.datetime(2019, 1, 1),
    )
    # Create an "old" datafile with "old" exam for cleanup method (must skip exam)
    old_exam_in_old_datafile = G(
        models.Exam,
        created=dt.datetime(2019, 1, 1),
        modified=dt.datetime(2019, 1, 1),
        datafile__created=dt.datetime(2019, 1, 1),
        datafile__modified=dt.datetime(2019, 1, 1),
    )

    models.RcoiUpdater().run()

    # assertions for cleanup method
    assert not models.Exam.objects.filter(pk=old_exam_in_updated_datafile.id).exists()
    assert models.Exam.objects.filter(pk=old_exam_in_old_datafile.id).exists()

    # +2 from G() +1 from run() -1 from cleanup = 2
    exam_count = models.Exam.objects.count()
    assert exam_count == 2

    # Second Run with the same data for branch coverage
    models.RcoiUpdater().run()
    exam_count = models.Exam.objects.count()
    assert exam_count == 2


def test_rcoi_updater_same_file(mocker_xlsx_to_csv_single_file, exam_file):
    """
    Test - DB Updater - must process update because last_modified field for file is different
    """

    exam_file_diff_date = exam_file.copy()
    exam_file_diff_date.update(
        {"last_modified": dt.datetime(2020, 6, 1, 1, 1, 1)},
    )

    G(models.DataSource)
    G(models.DataFile, modified=dt.datetime(2019, 1, 1), **exam_file_diff_date)

    models.RcoiUpdater().run()
    exam_count = models.Exam.objects.count()
    assert exam_count == 1


def test_rcoi_updater_if_tmp_path_exists(mocker_xlsx_to_csv_simple, mocker):
    """
    Test - DB Updater - tmp_path exists ('if' branch coverage)
    """
    mocker.patch.object(Path, "exists", return_value=True)
    mocker.patch("shutil.rmtree")

    G(models.DataSource)
    models.RcoiUpdater().run()
    exam_count = models.Exam.objects.count()
    assert exam_count == 1
    mocker.resetall()


def test_rcoi_updater_if_data_is_none(mocker_rcoi_updater):
    """
    Test - DB Updater - data is None ('if' branch coverage)
    """
    models.RcoiUpdater().run()
    exam_count = models.Exam.objects.count()
    assert exam_count == 0


def test_rcoi_updater_if_data_is_bad(mocker):
    """
    Test - DB Updater - data is bad ('except' branch coverage)
    """
    mocker.patch("apps.rcoi.models.RcoiUpdater.__init__", return_value=None)
    mocker.patch("apps.rcoi.models.RcoiUpdater.data", "unexpected_data", create=True)

    with pytest.raises(Exception):  # noqa: B017 PT011
        models.RcoiUpdater().run()
    mocker.resetall()


def test_rcoi_updater_partial_source_failure(mocker, exams_csv, exam_file):
    """
    Test - DB Updater - one source fails, other sources are still processed
    """
    G(models.DataSource, name="broken", url="http://broken")
    G(models.DataSource, name="working", url="http://working")

    def fake_get_files_info(url):
        if url == "http://broken":
            msg = "connection failed"
            raise ConnectionError(msg)
        return [exam_file]

    mocker.patch(
        "apps.rcoi.xlsx_to_csv.get_files_info",
        side_effect=fake_get_files_info,
    )
    mocker.patch("apps.rcoi.xlsx_to_csv.download_file")
    mocker.patch("apps.rcoi.xlsx_to_csv.save_to_stream", return_value=exams_csv)

    updater = models.RcoiUpdater()
    assert updater.run() is True

    assert models.Exam.objects.count() == 1
    assert models.DataFile.objects.filter(name=exam_file["name"]).exists()
    assert len(updater.errors) == 1
    error = updater.errors[0]
    assert error["source"] == "broken"
    assert error["stage"] == "get_files_info"
    assert "connection failed" in error["error"]


def test_rcoi_updater_all_sources_failed(mocker):
    """
    Test - DB Updater - all sources fail, nothing is written to db
    """
    G(models.DataSource, name="broken1", url="http://broken1")
    G(models.DataSource, name="broken2", url="http://broken2")
    mocker.patch(
        "apps.rcoi.xlsx_to_csv.get_files_info",
        side_effect=ConnectionError("connection failed"),
    )

    with pytest.raises(models.RcoiUpdateError) as exc_info:
        models.RcoiUpdater()

    assert len(exc_info.value.errors) == 2
    assert models.Exam.objects.count() == 0
    assert models.DataFile.objects.count() == 0


def test_rcoi_updater_all_downloads_failed(mocker, exam_file):
    """
    Test - DB Updater - all downloads fail, nothing is written to db
    """
    G(models.DataSource, name="source", url="http://source")
    mocker.patch(
        "apps.rcoi.xlsx_to_csv.get_files_info",
        return_value=[exam_file],
    )
    mocker.patch(
        "apps.rcoi.xlsx_to_csv.download_file",
        side_effect=ConnectionError("connection failed"),
    )

    with pytest.raises(models.RcoiUpdateError) as exc_info:
        models.RcoiUpdater()

    assert len(exc_info.value.errors) == 1
    assert exc_info.value.errors[0]["stage"] == "download_file"
    assert models.Exam.objects.count() == 0
    assert not models.DataFile.objects.filter(name=exam_file["name"]).exists()


def test_rcoi_updater_single_file_download_failure(mocker, exams_csv, exam_file):
    """
    Test - DB Updater - failed file download does not affect other files of the same source
    """
    exam_file_2 = exam_file.copy()
    exam_file_2.update(
        {
            "name": "2020-06-14__11__.xlsx",
            "url": "https://rcoi.mcko.ru/2020-06-14__11__rab_ppe_test.xlsx",
        },
    )
    G(models.DataSource, name="source", url="http://source")

    mocker.patch(
        "apps.rcoi.xlsx_to_csv.get_files_info",
        return_value=[exam_file, exam_file_2],
    )

    def fake_download_file(url, name, path):
        if name == exam_file_2["name"]:
            msg = "file not found"
            raise FileNotFoundError(msg)

    mocker.patch(
        "apps.rcoi.xlsx_to_csv.download_file",
        side_effect=fake_download_file,
    )
    mocker.patch("apps.rcoi.xlsx_to_csv.save_to_stream", return_value=exams_csv)

    updater = models.RcoiUpdater()
    assert updater.run() is True

    assert models.Exam.objects.count() == 1
    assert models.DataFile.objects.filter(name=exam_file["name"]).exists()
    assert not models.DataFile.objects.filter(name=exam_file_2["name"]).exists()
    assert len(updater.errors) == 1
    error = updater.errors[0]
    assert error["source"] == exam_file_2["name"]
    assert error["stage"] == "download_file"
    assert "file not found" in error["error"]


def test_exam_importer(mocker_xlsx_to_csv_simple, exam_file_diff_date):
    """
    Test - Exam Importer
    """
    exam_url = "http://123"
    exam_date = dt.date(2020, 6, 1)
    exam_level = "123"

    # no file
    r1 = models.ExamImporter(exam_url, exam_date, exam_level)
    assert len(r1.data) == 9

    # same file
    r2 = models.ExamImporter(exam_url, exam_date, exam_level)
    assert r2.data is None

    # updated file
    mocker_xlsx_to_csv_simple.patch(
        "apps.rcoi.xlsx_to_csv.prepare_file_info",
        return_value=exam_file_diff_date,
    )
    r3 = models.ExamImporter(exam_url, exam_date, exam_level)
    # because of mocker, after first read stream stays at the end
    assert len(r3.data) == 0
