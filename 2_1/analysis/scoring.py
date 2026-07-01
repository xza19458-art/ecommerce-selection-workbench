"""Early product-selection scoring model."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class ProductScore:
    total_score: float
    demand_score: float
    growth_score: float
    competition_score: float
    rating_score: float
    price_score: float
    rank_score: float
    reason: str


def score_record(record: Any) -> ProductScore:
    demand_score = _score_demand(record.monthly_bought)
    growth_score = 50.0
    competition_score = _score_competition(record.review_count)
    rating_score = _score_rating(record.rating)
    price_score = _score_price(record.price)
    rank_confidence = getattr(record, "rank_confidence", "unknown")
    rank_score = _score_rank(record.organic_rank, rank_confidence)

    total = (
        demand_score * 0.25
        + growth_score * 0.15
        + competition_score * 0.20
        + rating_score * 0.15
        + price_score * 0.15
        + rank_score * 0.10
    )
    total = round(_clamp(total), 2)

    reasons = [
        f"近月购买量约 {record.monthly_bought}，需求得分 {demand_score:.0f}",
        f"评论数 {record.review_count}，竞争得分 {competition_score:.0f}",
        f"评分 {record.rating:.1f}，评分质量得分 {rating_score:.0f}",
        f"价格 ${record.price:.2f}，价格带得分 {price_score:.0f}",
    ]
    if record.organic_rank and _rank_confidence_is_usable(rank_confidence):
        reasons.append(f"当前自然序位估算 {record.organic_rank}，序位得分 {rank_score:.0f}")
    elif record.organic_rank:
        reasons.append("当前搜索位置置信度不足，序位得分按中性处理")
    if record.is_deal:
        reasons.append("当前存在促销信号，需后续观察是否为短期拉升")

    return ProductScore(
        total_score=total,
        demand_score=round(demand_score, 2),
        growth_score=round(growth_score, 2),
        competition_score=round(competition_score, 2),
        rating_score=round(rating_score, 2),
        price_score=round(price_score, 2),
        rank_score=round(rank_score, 2),
        reason="；".join(reasons),
    )


def _score_demand(monthly_bought: int | None) -> float:
    if not monthly_bought:
        return 0.0
    return _clamp(math.log10(monthly_bought + 1) / math.log10(20000) * 100)


def _score_competition(review_count: int | None) -> float:
    if review_count is None:
        return 0.0
    if review_count <= 50:
        return 100.0
    if review_count >= 3000:
        return 10.0
    return _clamp(100 - math.log10(review_count) / math.log10(3000) * 85)


def _score_rating(rating: float | None) -> float:
    if rating is None:
        return 0.0
    if rating < 3.8:
        return 20.0
    return _clamp((rating - 3.8) / 1.2 * 100)


def _score_price(price: float | None) -> float:
    if price is None or price <= 0:
        return 0.0
    if 15 <= price <= 60:
        return 100.0
    if 10 <= price < 15:
        return 75.0
    if 60 < price <= 100:
        return 80.0
    if 5 <= price < 10:
        return 45.0
    return 35.0


def _score_rank(rank: int | None, confidence: str | None = None) -> float:
    if confidence is not None and not _rank_confidence_is_usable(confidence):
        return 50.0
    if not rank:
        return 50.0
    if rank <= 10:
        return 100.0
    if rank >= 100:
        return 25.0
    return _clamp(100 - (rank - 10) / 90 * 75)


def _rank_confidence_is_usable(confidence: str | None) -> bool:
    return confidence in {None, "", "page_first", "batch_continuous"}


def _clamp(value: float, min_value: float = 0.0, max_value: float = 100.0) -> float:
    return max(min_value, min(max_value, value))
