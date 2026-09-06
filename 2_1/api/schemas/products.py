"""Schemas for product pool, detail context, trend, and advice APIs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductPoolQuery(BaseModel):
    limit: int = 100
    offset: int = 0
    keyword: str | None = None
    keyword_exact: bool = False
    keyword_scope: Literal["current", "observed"] = "current"
    min_score: float | None = None
    max_score: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    min_rating: float | None = None
    max_rating: float | None = None
    min_reviews: int | None = None
    max_reviews: int | None = None
    min_bought: int | None = None
    max_bought: int | None = None
    min_rank: int | None = None
    max_rank: int | None = None
    deal_status: Literal["all", "deal", "regular"] = "all"
    size_status: Literal["all", "known", "missing"] = "all"
    sort_by: str = "total_score"
    sort_dir: str = "desc"


class ProductPoolPageData(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    limit: int
    offset: int
    sort_by: str
    sort_dir: str
    keyword_scope: Literal["current", "observed"] | None = None
    scope_message: str | None = None


class ProductDetailData(BaseModel):
    model_config = ConfigDict(extra="allow")

    product: dict[str, Any] | None
    snapshots: list[dict[str, Any]] = Field(default_factory=list)


class ProductAdviceData(BaseModel):
    conclusion: str
    risk: str
    entry_strategy: str


class ProductMetricTrendData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    key: str
    start: float | None
    end: float | None
    direction: str
    change_ratio: float | None
    is_improvement: bool | None


class ProductTrendData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sample_size: int
    span_days: float
    confidence: str
    confidence_score: float
    growth_score: float
    metrics: list[ProductMetricTrendData] = Field(default_factory=list)
    promo_warning: str | None = None
    summary: str
