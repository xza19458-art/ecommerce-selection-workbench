"""Versioned seller-domain scoring model routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.contracts import ok
from services.domain_scoring import (
    create_domain_profile,
    create_domain_profile_version,
    evaluate_domain_profile_batch,
    evaluate_product_with_domain_profile,
    fetch_domain_profiles_page,
    get_domain_profile,
    get_domain_scoring_catalog,
    set_domain_profile_current_version,
    set_domain_profile_status,
    update_domain_profile,
)


router = APIRouter(prefix="/api/domain-models", tags=["domain-models"])


class DomainProfileCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    marketplace: str = Field(default="US", min_length=2, max_length=16)
    description: str | None = Field(default=None, max_length=20_000)
    scope_type: str = "marketplace"
    category_scope: str | None = Field(default=None, max_length=512)
    niche_id: int | None = Field(default=None, ge=1)
    base_strategy: str = "balanced"
    config: dict[str, Any] | None = None
    change_note: str | None = Field(default=None, max_length=1000)


class DomainProfileUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=20_000)


class DomainProfileVersionIn(BaseModel):
    scope_type: str
    category_scope: str | None = Field(default=None, max_length=512)
    niche_id: int | None = Field(default=None, ge=1)
    config: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=1000)


class DomainProfileStatusIn(BaseModel):
    status: str


class DomainProfileCurrentVersionIn(BaseModel):
    version_id: int = Field(ge=1)


class DomainProfileEvaluationIn(BaseModel):
    asin: str = Field(min_length=10, max_length=10)
    keyword: str | None = Field(default=None, max_length=255)
    version_id: int | None = Field(default=None, ge=1)


class DomainProfileBatchEvaluationIn(BaseModel):
    version_id: int | None = Field(default=None, ge=1)
    search: str = Field(default="", max_length=255)
    evidence_scope: str = "all"
    signal: str = "all"
    min_abs_delta: float = Field(default=0.0, ge=0.0, le=100.0)
    sort_by: str = "abs_delta"
    sort_dir: str = "desc"
    sample_limit: int = Field(default=50, ge=1, le=50)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


@router.get("/catalog")
def domain_model_catalog() -> dict[str, Any]:
    return ok(get_domain_scoring_catalog())


@router.get("")
def domain_models(
    marketplace: str = "US",
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return ok(
        fetch_domain_profiles_page(
            marketplace=marketplace,
            status=status,
            limit=limit,
            offset=offset,
        )
    )


@router.post("")
def domain_model_create(body: DomainProfileCreateIn) -> dict[str, Any]:
    return ok(create_domain_profile(**body.model_dump()))


@router.get("/{profile_id}")
def domain_model_detail(profile_id: int) -> dict[str, Any]:
    return ok(get_domain_profile(profile_id))


@router.patch("/{profile_id}")
def domain_model_update(profile_id: int, body: DomainProfileUpdateIn) -> dict[str, Any]:
    return ok(update_domain_profile(profile_id, **body.model_dump(exclude_unset=True)))


@router.post("/{profile_id}/versions")
def domain_model_create_version(profile_id: int, body: DomainProfileVersionIn) -> dict[str, Any]:
    return ok(create_domain_profile_version(profile_id, **body.model_dump()))


@router.post("/{profile_id}/status")
def domain_model_status(profile_id: int, body: DomainProfileStatusIn) -> dict[str, Any]:
    return ok(set_domain_profile_status(profile_id, body.status))


@router.post("/{profile_id}/current-version")
def domain_model_current_version(
    profile_id: int,
    body: DomainProfileCurrentVersionIn,
) -> dict[str, Any]:
    return ok(set_domain_profile_current_version(profile_id, body.version_id))


@router.post("/{profile_id}/evaluate-product")
def domain_model_evaluate_product(
    profile_id: int,
    body: DomainProfileEvaluationIn,
) -> dict[str, Any]:
    return ok(evaluate_product_with_domain_profile(profile_id, **body.model_dump()))


@router.post("/{profile_id}/evaluate-batch")
def domain_model_evaluate_batch(
    profile_id: int,
    body: DomainProfileBatchEvaluationIn,
) -> dict[str, Any]:
    return ok(evaluate_domain_profile_batch(profile_id, **body.model_dump()))
