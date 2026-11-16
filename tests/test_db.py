from pathlib import Path

from src.constants import DB_FILE
from src.utils import db
from src.views.job import Job


def test_db_is_isolated_in_tests(tmp_path):
    """Тесты работают с временной БД, а не с файлом из data_folder."""
    assert db.DB_FILE != DB_FILE
    assert Path(db.DB_FILE).is_relative_to(tmp_path)


def test_db_file_and_tables_created_on_first_access():
    """Файл БД и таблицы создаются при первом обращении, папка — автоматически."""
    assert not Path(db.DB_FILE).exists()

    assert db.get_job_from_db("12345") is None
    assert Path(db.DB_FILE).exists()


def test_save_and_load_job_with_skills_list():
    """Список навыков сохраняется в JSON-колонку и читается обратно списком."""
    job = Job(vacancy_id="1", job_title="Python Dev", skills=["python", "docker"])

    db.save_job_to_db(job)

    assert db.get_job_from_db("1").skills == ["python", "docker"]


def test_save_same_vacancy_twice_updates_row():
    """Повторное сохранение вакансии обновляет запись, а не падает на уникальности ID."""
    db.save_job_to_db(Job(vacancy_id="1", job_title="Старое название"))
    db.save_job_to_db(Job(vacancy_id="1", job_title="Новое название"))

    assert db.get_job_from_db("1").job_title == "Новое название"


def test_extra_job_fields_are_not_saved():
    """Лишние поля Job (extra="allow") не ломают сохранение."""
    saved = db.save_job_to_db(Job(vacancy_id="1", url="https://hh.ru/vacancy/1"))

    assert saved.vacancy_id == "1"
