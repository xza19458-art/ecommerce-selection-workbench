"""Keyword-level market aggregates derived from one saved SERP batch."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from typing import Any, Iterable

from parsers.amazon_search_parser import AmazonProductRecord


@dataclass(frozen=True)
class SerpSnapshot:
    snapshot_at: datetime
    page_count: int
    total_card_count: int
    organic_count: int
    sponsored_count: int
    unique_asin_count: int
    ad_density: float | None
    price_p25: float | None
    price_median: float | None
    price_p75: float | None
    review_p25: float | None
    review_median: float | None
    review_p75: float | None
    rating_median: float | None
    monthly_bought_median: float | None
    demand_cr3: float | None
    demand_cr10: float | None
    data_coverage: float | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregate_serp_snapshot(
    valid_records: Iterable[AmazonProductRecord],
    rejected_records: Iterable[AmazonProductRecord],
) -> SerpSnapshot | None:
    """Aggregate raw cards while using unique organic ASINs for distributions."""
    cards = [*valid_records, *rejected_records]
    if not cards:
        return None
    cards.sort(key=_record_order)
    organic_cards = [record for record in cards if not record.is_sponsored]
    sponsored_cards = [record for record in cards if record.is_sponsored]
    organic_unique = _first_by_asin(organic_cards)

    prices = _numbers(record.price for record in organic_unique)
    reviews = _numbers(record.review_count for record in organic_unique)
    ratings = _numbers(record.rating for record in organic_unique)
    monthly = _numbers(record.monthly_bought for record in organic_unique)
    total_cards = len(cards)
    unique_asins = {record.asin for record in cards if record.asin}
    page_keys = {
        str(record.source_file or f"page:{record.page_no}")
        for record in cards
        if record.source_file or record.page_no is not None
    }
    snapshot_at = min(record.snapshot_at for record in cards)
    field_names = ("price", "rating", "review_count", "monthly_bought")
    field_counts = {
        field: sum(getattr(record, field) is not None for record in organic_unique)
        for field in field_names
    }
    denominator = len(organic_unique) * len(field_names)
    coverage = sum(field_counts.values()) / denominator if denominator else None
    page_numbers = sorted({record.page_no for record in cards if record.page_no is not None})

    return SerpSnapshot(
        snapshot_at=snapshot_at,
        page_count=len(page_keys) or len(page_numbers) or 1,
        total_card_count=total_cards,
        organic_count=len(organic_cards),
        sponsored_count=len(sponsored_cards),
        unique_asin_count=len(unique_asins),
        ad_density=round(len(sponsored_cards) / total_cards, 4) if total_cards else None,
        price_p25=_percentile(prices, 0.25),
        price_median=_percentile(prices, 0.50),
        price_p75=_percentile(prices, 0.75),
        review_p25=_percentile(reviews, 0.25),
        review_median=_percentile(reviews, 0.50),
        review_p75=_percentile(reviews, 0.75),
        rating_median=_percentile(ratings, 0.50),
        monthly_bought_median=_percentile(monthly, 0.50),
        demand_cr3=_concentration(monthly, 3),
        demand_cr10=_concentration(monthly, 10),
        data_coverage=round(coverage, 4) if coverage is not None else None,
        raw={
            "basis": "unique organic ASINs for distributions; all cards for ad density",
            "page_numbers": page_numbers,
            "field_non_null_counts": field_counts,
            "organic_unique_count": len(organic_unique),
            "rejected_card_count": len([record for record in cards if record.reject_reasons]),
        },
    )


def upsert_serp_snapshot(cursor: Any, keyword_id: int, snapshot: SerpSnapshot) -> None:
    cursor.execute(
        """
        INSERT INTO keyword_serp_snapshots (
          keyword_id, snapshot_at, page_count, total_card_count, organic_count,
          sponsored_count, unique_asin_count, ad_density,
          price_p25, price_median, price_p75,
          review_p25, review_median, review_p75,
          rating_median, monthly_bought_median, demand_cr3, demand_cr10,
          data_coverage, raw_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          page_count = VALUES(page_count),
          total_card_count = VALUES(total_card_count),
          organic_count = VALUES(organic_count),
          sponsored_count = VALUES(sponsored_count),
          unique_asin_count = VALUES(unique_asin_count),
          ad_density = VALUES(ad_density),
          price_p25 = VALUES(price_p25),
          price_median = VALUES(price_median),
          price_p75 = VALUES(price_p75),
          review_p25 = VALUES(review_p25),
          review_median = VALUES(review_median),
          review_p75 = VALUES(review_p75),
          rating_median = VALUES(rating_median),
          monthly_bought_median = VALUES(monthly_bought_median),
          demand_cr3 = VALUES(demand_cr3),
          demand_cr10 = VALUES(demand_cr10),
          data_coverage = VALUES(data_coverage),
          raw_json = VALUES(raw_json)
        """,
        (
            keyword_id,
            snapshot.snapshot_at,
            snapshot.page_count,
            snapshot.total_card_count,
            snapshot.organic_count,
            snapshot.sponsored_count,
            snapshot.unique_asin_count,
            snapshot.ad_density,
            snapshot.price_p25,
            snapshot.price_median,
            snapshot.price_p75,
            snapshot.review_p25,
            snapshot.review_median,
            snapshot.review_p75,
            snapshot.rating_median,
            snapshot.monthly_bought_median,
            snapshot.demand_cr3,
            snapshot.demand_cr10,
            snapshot.data_coverage,
            json.dumps(snapshot.raw, ensure_ascii=False),
        ),
    )


def _record_order(record: AmazonProductRecord) -> tuple[Any, ...]:
    return (
        record.page_no if record.page_no is not None else 1_000_000,
        record.result_slot if record.result_slot is not None else 1_000_000,
        record.source_file or "",
        record.asin or "",
    )


def _first_by_asin(records: Iterable[AmazonProductRecord]) -> list[AmazonProductRecord]:
    result: list[AmazonProductRecord] = []
    seen: set[str] = set()
    for record in records:
        if not record.asin or record.asin in seen:
            continue
        seen.add(record.asin)
        result.append(record)
    return result


def _numbers(values: Iterable[Any]) -> list[float]:
    result: list[float] = []
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


def _concentration(values: list[float], top_n: int) -> float | None:
    positive = sorted((value for value in values if value > 0), reverse=True)
    total = sum(positive)
    if total <= 0:
        return None
    return round(sum(positive[:top_n]) / total, 4)
