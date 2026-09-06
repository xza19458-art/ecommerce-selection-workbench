from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from parsers.amazon_search_parser import AmazonProductRecord
from services.serp_metrics import aggregate_serp_snapshot


SNAPSHOT_AT = datetime(2026, 7, 12, 10, 0, 0)


def _record(
    asin: str,
    *,
    price: float | None,
    rating: float | None,
    reviews: int | None,
    monthly: int | None,
    sponsored: bool = False,
    page: int = 1,
    slot: int = 1,
    rejected: bool = False,
) -> AmazonProductRecord:
    record = AmazonProductRecord(
        marketplace="US",
        asin=asin,
        title=f"Product {asin}",
        product_url=f"https://www.amazon.com/dp/{asin}",
        image_url="https://images-na.ssl-images-amazon.com/image.jpg",
        price=price,
        rating=rating,
        review_count=reviews,
        monthly_bought=monthly,
        is_deal=False,
        is_sponsored=sponsored,
        page_no=page,
        organic_rank=None,
        snapshot_at=SNAPSHOT_AT,
        result_slot=slot,
        source_file=f"page-{page}.html",
    )
    if rejected:
        record.reject_reasons.append("缺少核心字段")
    return record


def test_serp_snapshot_keeps_ads_out_of_distributions() -> None:
    valid = [
        _record("B000000001", price=10, rating=4, reviews=100, monthly=100, slot=1),
        _record("B000000002", price=20, rating=5, reviews=300, monthly=300, slot=2),
    ]
    rejected = [
        _record(
            "B000000003",
            price=30,
            rating=5,
            reviews=5000,
            monthly=9000,
            sponsored=True,
            slot=3,
            rejected=True,
        ),
        _record(
            "B000000004",
            price=None,
            rating=4,
            reviews=None,
            monthly=None,
            page=2,
            slot=1,
            rejected=True,
        ),
    ]

    result = aggregate_serp_snapshot(valid, rejected)

    assert result is not None
    assert result.page_count == 2
    assert result.total_card_count == 4
    assert result.organic_count == 3
    assert result.sponsored_count == 1
    assert result.unique_asin_count == 4
    assert result.ad_density == 0.25
    assert result.price_p25 == 12.5
    assert result.price_median == 15.0
    assert result.price_p75 == 17.5
    assert result.review_median == 200.0
    assert result.monthly_bought_median == 200.0
    assert result.demand_cr3 == 1.0
    assert result.demand_cr10 == 1.0
    assert result.data_coverage == 0.75


if __name__ == "__main__":
    test_serp_snapshot_keeps_ads_out_of_distributions()
    print("serp metrics tests passed: 1/1")
