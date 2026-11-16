from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WelcomeResponse(BaseModel):
    name: str
    description: str
    company: str
    website: str
    docs_url: str
    redoc_url: str
    openapi_url: str
    health_url: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    timestamp: datetime


class ProjectResponse(BaseModel):
    name: str
    company: str
    website: str
    version: str
    api_prefix: str
    features: list[str]


class CollectBatchRequest(BaseModel):
    # 入参校验在路由内手工完成（含取值范围与 cursor 解析），以便非法入参
    # 统一返回 400 而不是 FastAPI/Pydantic 默认的 422。
    model_config = ConfigDict(extra="ignore")

    source: Any = None
    cursor: Any = None
    limit: Any = None


class CollectBatchResponse(BaseModel):
    batch_id: str
    items: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool
    fetched_count: int
    status: Literal["ok", "partial", "failed"] = Field(
        description="ok=全部取到；partial=数据源中途报错但已有记录；failed=一条都没取到"
    )


class ErrorResponse(BaseModel):
    detail: str
