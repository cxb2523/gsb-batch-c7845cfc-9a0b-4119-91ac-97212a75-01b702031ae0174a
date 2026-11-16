from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, AliasChoices


class Job(BaseModel):
    """
    Job data structure representing a scraped vacancy.

    Matches the dictionary constructed in `JobApplier.scrape_vacancy`.
    """

    model_config = ConfigDict(extra="allow")

    # Basic info from search result
    job_title: str = ""
    vacancy_id: str = Field(
        validation_alias=AliasChoices("vacancy_id", "id"),
        serialization_alias="id",
    )
    company_id: Optional[str] = None
    company_name: str = "Unknown"

    # Full info fields from vacancy page
    salary: str = ""
    experience: str = ""
    employment: str = ""
    hiring_formats: str = ""
    schedule: str = ""
    working_hours: str = ""
    work_formats: str = ""
    description: str = ""
    skills: List[str] = []


class JobDescription(BaseModel):
    """
    Job description data structure representing a scraped vacancy description
    that is used in search mode.
    """

    model_config = ConfigDict(extra="allow")

    job_title: str = ""
    company_name: str = ""
    vacancy_id: Optional[str] = None
    link: str = ""
    skills: List[str] = []
    cover_letter: str = ""
    job_score: int = 0
