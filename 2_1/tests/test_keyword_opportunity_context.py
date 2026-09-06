from __future__ import annotations

from decimal import Decimal
import logging
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.keyword_opportunities import (
    _enrich_row,
    _keyword_opportunity_mysql_ctes,
    _keyword_opportunity_mysql_select,
    _keyword_opportunity_warehouse_ctes,
    _keyword_opportunity_warehouse_select,
)
import services.keyword_opportunities as keyword_opportunities


def test_sponsored_count_uses_keyword_rank_context_only() -> None:
    mysql_sql = _keyword_opportunity_mysql_select("", "")
    warehouse_sql = _keyword_opportunity_warehouse_select("", "")

    assert "krs.is_sponsored = 1" in mysql_sql
    assert "krs.is_sponsored = 1" in warehouse_sql
    assert "snap.is_sponsored" not in mysql_sql
    assert "snap.is_sponsored" not in warehouse_sql


def test_opportunity_uses_latest_complete_keyword_batch_and_aligned_snapshots() -> None:
    mysql_ctes = _keyword_opportunity_mysql_ctes()
    warehouse_ctes = _keyword_opportunity_warehouse_ctes()
    mysql_sql = _keyword_opportunity_mysql_select("", "")
    warehouse_sql = _keyword_opportunity_warehouse_select("", "")

    assert "PARTITION BY keyword_id" in mysql_ctes
    assert "batch.current_snapshot_at = krs.snapshot_at" in mysql_ctes
    assert "batch.current_snapshot_at = rank.snapshot_at" in warehouse_ctes
    assert "snap.snapshot_at = krs.snapshot_at" in mysql_sql
    assert "snap.snapshot_at = krs.snapshot_at" in warehouse_sql
    assert "historical_product_count" in mysql_sql
    assert "entered_product_count" in warehouse_sql
    assert "rank_changed_count" in mysql_sql


def test_two_batch_transition_is_labeled_low_confidence() -> None:
    row = _enrich_row(
        {
            "product_count": 46,
            "historical_product_count": 51,
            "observation_count": 2,
            "previous_snapshot_at": "2026-07-20 19:00:00",
            "retained_product_count": 40,
            "entered_product_count": 6,
            "exited_product_count": 5,
            "comparable_rank_count": 40,
            "rank_changed_count": 38,
        }
    )

    assert row["aggregation_scope"] == "latest_keyword_batch"
    assert "留存 40 个" in row["transition_summary"]
    assert "新进入 6 个" in row["transition_summary"]
    assert "两个相邻采集时点" in row["risk_warnings"]


def test_warehouse_failure_falls_back_to_mysql_with_visible_log(monkeypatch, caplog) -> None:
    calls: list[tuple[str, dict]] = []

    def warehouse(**kwargs):
        calls.append(("warehouse", kwargs))
        raise RuntimeError("broken DuckDB view")

    def mysql(**kwargs):
        calls.append(("mysql", kwargs))
        return {
            "rows": [
                {
                    "keyword_id": 7,
                    "marketplace": "US",
                    "keyword": "squishy toys",
                    "product_count": 5,
                    "avg_total_score": Decimal("73.5"),
                    "avg_demand_score": Decimal("80"),
                    "avg_competition_score": Decimal("70"),
                    "avg_rating_score": Decimal("90"),
                    "avg_price_score": Decimal("75"),
                    "avg_rank_score": Decimal("60"),
                    "avg_monthly_bought": Decimal("1200"),
                    "total_monthly_bought": Decimal("6000"),
                    "avg_review_count": Decimal("500"),
                    "avg_price": Decimal("22.5"),
                    "top10_count": 1,
                    "sponsored_count": 1,
                }
            ],
            "total": 1,
            "limit": 25,
            "offset": 50,
            "sort_by": "opportunity_score",
            "sort_dir": "desc",
        }

    monkeypatch.setattr(keyword_opportunities, "_fetch_keyword_opportunities_page_from_warehouse", warehouse)
    monkeypatch.setattr(keyword_opportunities, "_fetch_keyword_opportunities_page_from_mysql", mysql)
    monkeypatch.setattr(keyword_opportunities, "get_warehouse_status", lambda: {"status": "current"})

    with caplog.at_level(logging.WARNING, logger=keyword_opportunities.__name__):
        page = keyword_opportunities.fetch_keyword_opportunities_page(
            limit=25,
            offset=50,
            keyword="squishy",
            min_products=3,
        )

    assert [name for name, _ in calls] == ["warehouse", "mysql"]
    assert calls[1][1]["client"] is None
    assert page["total"] == 1
    assert page["rows"][0]["opportunity_score"] == 76.0
    assert page["rows"][0]["opportunity_level"] == "高机会"
    assert "falling back to MySQL" in caplog.text


def test_stale_warehouse_is_skipped_before_mysql_fallback(monkeypatch, caplog) -> None:
    calls: list[str] = []

    def warehouse(**kwargs):
        calls.append("warehouse")
        raise AssertionError("stale warehouse must not be queried")

    def mysql(**kwargs):
        calls.append("mysql")
        return {
            "rows": [],
            "total": 0,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "sort_by": kwargs["sort_by"],
            "sort_dir": kwargs["sort_dir"],
        }

    monkeypatch.setattr(keyword_opportunities, "get_warehouse_status", lambda: {"status": "stale"})
    monkeypatch.setattr(keyword_opportunities, "_fetch_keyword_opportunities_page_from_warehouse", warehouse)
    monkeypatch.setattr(keyword_opportunities, "_fetch_keyword_opportunities_page_from_mysql", mysql)

    with caplog.at_level(logging.WARNING, logger=keyword_opportunities.__name__):
        page = keyword_opportunities.fetch_keyword_opportunities_page(limit=25, offset=50)

    assert calls == ["mysql"]
    assert page["total"] == 0
    assert "skipping stale replica" in caplog.text
