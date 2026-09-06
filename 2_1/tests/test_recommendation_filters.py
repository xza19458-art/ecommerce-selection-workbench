from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.recommendations import _recommendation_filters  # noqa: E402


def test_recommendation_filters_cover_search_ranges_and_statuses() -> None:
    sql, params = _recommendation_filters(
        keyword="squishy",
        min_score=60,
        max_score=90,
        min_price=8,
        max_price=35,
        min_rating=4,
        max_rating=5,
        min_reviews=10,
        max_reviews=500,
        min_bought=50,
        max_bought=10000,
        min_rank=1,
        max_rank=60,
        deal_status="regular",
        size_status="missing",
        has_title_zh=True,
        has_product_size=True,
    )

    assert sql.startswith("AND ")
    assert "p.asin LIKE %s" in sql
    assert "p.title_zh LIKE %s" in sql
    assert "p.product_size LIKE %s" in sql
    assert "ps.total_score >= %s" in sql
    assert "ps.total_score <= %s" in sql
    assert "snap.price >= %s" in sql
    assert "snap.price <= %s" in sql
    assert "snap.rating >= %s" in sql
    assert "snap.rating <= %s" in sql
    assert "snap.review_count >= %s" in sql
    assert "snap.review_count <= %s" in sql
    assert "snap.monthly_bought >= %s" in sql
    assert "snap.monthly_bought <= %s" in sql
    assert "krs.organic_rank >= %s" in sql
    assert "krs.organic_rank <= %s" in sql
    assert "COALESCE(snap.is_deal, 0) = 0" in sql
    assert "NULLIF(TRIM(p.product_size), '') IS NULL" in sql
    assert params == [
        "%squishy%",
        "%squishy%",
        "%squishy%",
        "%squishy%",
        "%squishy%",
        60,
        90,
        8,
        35,
        4,
        5,
        10,
        500,
        50,
        10000,
        1,
        60,
    ]


def test_known_size_filter_is_empty_when_column_is_unavailable() -> None:
    sql, params = _recommendation_filters(size_status="known", has_product_size=False)

    assert sql == "AND 1 = 0"
    assert params == []
