import os
from functools import cache
from typing import List, Optional

from sqlalchemy import JSON, Engine, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session

from src.constants import DB_FILE
from src.views.job import Job


class Base(DeclarativeBase):
    pass

class JobORM(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    
    job_title: Mapped[str]
    
    company_name: Mapped[str]
    company_id: Mapped[Optional[str]]
    salary: Mapped[str]
    experience: Mapped[str]
    employment: Mapped[str]
    hiring_formats: Mapped[str]
    schedule: Mapped[str]
    working_hours: Mapped[str]
    work_formats: Mapped[str]
    description: Mapped[str]
    skills: Mapped[list[str]] = mapped_column(JSON)


@cache
def _get_engine(db_file: str) -> Engine:
    """Подключается к БД при первом обращении и создаёт таблицы, если их нет."""
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    return engine


def save_job_to_db(job: Job) -> Job:
    """Сохраняем информацию о вакансии в локальную БД"""
    with Session(_get_engine(DB_FILE)) as s:
        row = JobORM(**job.model_dump(by_alias=True, include=set(Job.model_fields)))
        row = s.merge(row)
        s.commit()
        s.refresh(row)
        return  Job.model_validate(row, from_attributes=True)

def get_job_from_db(vacancy_id: int) -> Job | None:
    """Загружаем информацию о вакансии из локальной БД"""
    with Session(_get_engine(DB_FILE)) as s:
        row = s.get(JobORM, vacancy_id)
        return Job.model_validate(row, from_attributes=True) if row else None


def get_all_jobs() -> List[Job]:
    with Session(_get_engine(DB_FILE)) as s:
        rows = s.scalars(select(JobORM)).all()
        return [Job.model_validate(r, from_attributes=True) for r in rows]

if __name__ == "__main__":
    res = get_all_jobs()
    print(res)