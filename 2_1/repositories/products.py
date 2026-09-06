"""Product read models and explicit single-product actions."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from services.product_advice import entry_strategy, risk_text, selection_conclusion
from services.product_image_cache import content_type_for, fetch_product_image
from services.product_pool import fetch_product_history, fetch_product_pool_page
from services.review_insights import fetch_product_review_insight
from services.trend_analysis import assess_product_trend


DetailCollector = Callable[[str], dict[str, Any]]


class ProductRepository:
    def __init__(self, *, detail_collector: DetailCollector | None = None) -> None:
        self._detail_collector = detail_collector

    def get_pool(self, **filters: Any) -> list[dict[str, Any]]:
        return self.get_pool_page(offset=0, **filters)["rows"]

    def get_pool_page(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        keyword: str | None = None,
        keyword_exact: bool = False,
        keyword_scope: str = "current",
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
        deal_status: str = "all",
        size_status: str = "all",
        sort_by: str = "total_score",
        sort_dir: str = "desc",
    ) -> dict[str, Any]:
        return fetch_product_pool_page(
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

    def get_history(self, asin: str, *, score_keyword: str | None = None) -> dict[str, Any]:
        detail = fetch_product_history(asin, score_keyword=score_keyword)
        # Preserve the established product-detail contract. Review development
        # remains a separate, frozen module; this only reads its existing result.
        detail["review_insight"] = fetch_product_review_insight(asin)
        return detail

    def get_trend(self, asin: str, *, score_keyword: str | None = None) -> Any:
        detail = self.get_history(asin, score_keyword=score_keyword)
        snapshots = detail.get("snapshots", []) if isinstance(detail, dict) else []
        return assess_product_trend(snapshots)

    def get_advice(self, asin: str, *, score_keyword: str | None = None) -> dict[str, str]:
        detail = self.get_history(asin, score_keyword=score_keyword)
        product = (detail.get("product") or {}) if isinstance(detail, dict) else {}
        snapshots = (detail.get("snapshots") or []) if isinstance(detail, dict) else []
        return {
            "conclusion": selection_conclusion(product, snapshots),
            "risk": risk_text(product, snapshots),
            "entry_strategy": entry_strategy(product, snapshots),
        }

    def collect_detail(self, asin: str) -> dict[str, Any]:
        if self._detail_collector is None:
            raise RuntimeError("商品详情采集入口尚未连接共享浏览器控制器")
        return self._detail_collector(asin)

    def get_image(self, asin: str, *, large: bool = False) -> tuple[Path, str] | None:
        path = fetch_product_image(asin, large=large)
        if path is None:
            return None
        return path, content_type_for(path)
