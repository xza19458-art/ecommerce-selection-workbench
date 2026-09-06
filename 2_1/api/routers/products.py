"""Product pool, detail, trend, advice, image, and explicit detail collection routes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from api.contracts import ok
from api.schemas.products import (
    ProductAdviceData,
    ProductDetailData,
    ProductPoolPageData,
    ProductPoolQuery,
    ProductTrendData,
)
from repositories.products import ProductRepository


router = APIRouter(prefix="/api/products", tags=["products"])
_repository = ProductRepository()


def configure_repository(repository: ProductRepository) -> None:
    global _repository
    _repository = repository


@router.get("")
def products(
    limit: int = 100,
    offset: int = 0,
    keyword: str | None = None,
    keyword_exact: bool = False,
    keyword_scope: Literal["current", "observed"] = "current",
    min_score: float | None = None,
    max_score: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    max_rating: float | None = None,
    min_reviews: int | None = None,
    max_reviews: int | None = None,
    min_bought: int | None = None,
    max_bought: int | None = None,
    min_rank: int | None = None,
    max_rank: int | None = None,
    deal_status: Literal["all", "deal", "regular"] = "all",
    size_status: Literal["all", "known", "missing"] = "all",
    sort_by: str = "total_score",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    query = ProductPoolQuery(
        limit=limit,
        offset=offset,
        keyword=keyword,
        keyword_exact=keyword_exact,
        keyword_scope=keyword_scope,
        min_score=min_score,
        max_score=max_score,
        min_price=min_price,
        max_price=max_price,
        min_rating=min_rating,
        max_rating=max_rating,
        min_reviews=min_reviews,
        max_reviews=max_reviews,
        min_bought=min_bought,
        max_bought=max_bought,
        min_rank=min_rank,
        max_rank=max_rank,
        deal_status=deal_status,
        size_status=size_status,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    data = ProductPoolPageData.model_validate(
        _repository.get_pool_page(**query.model_dump())
    ).model_dump()
    return ok(data)


@router.get("/{asin}")
def product_detail(asin: str, score_keyword: str | None = None) -> dict[str, Any]:
    data = ProductDetailData.model_validate(
        _repository.get_history(asin, score_keyword=score_keyword)
    ).model_dump()
    return ok(data)


@router.post("/{asin}/collect-detail")
def product_detail_collect(asin: str) -> dict[str, Any]:
    # This remains an explicit one-product action. The injected callback uses
    # the same shared browser and lock as search collection.
    return ok(_repository.collect_detail(asin))


@router.get("/{asin}/image")
def product_image(asin: str, large: bool = False):
    image = _repository.get_image(asin, large=large)
    if image is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "data": None, "message": "无可用商品图"},
        )
    path, media_type = image
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/{asin}/trend")
def product_trend(asin: str, score_keyword: str | None = None) -> dict[str, Any]:
    data = ProductTrendData.model_validate(
        _repository.get_trend(asin, score_keyword=score_keyword)
    ).model_dump()
    return ok(data)


@router.get("/{asin}/advice")
def product_advice(asin: str, score_keyword: str | None = None) -> dict[str, Any]:
    data = ProductAdviceData.model_validate(
        _repository.get_advice(asin, score_keyword=score_keyword)
    ).model_dump()
    return ok(data)
