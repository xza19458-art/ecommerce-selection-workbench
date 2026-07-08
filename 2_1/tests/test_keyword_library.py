from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.keyword_library import (  # noqa: E402
    _build_asset_filters,
    _build_keyword_tree,
    _normalize_asset_row,
    create_tracking_for_keywords,
)


def test_normalize_asset_row_adds_booleans_and_tracking_status() -> None:
    row = {
        "keyword_id": "12",
        "marketplace": "US",
        "keyword": "stress ball",
        "product_count": "8",
        "snapshot_time_count": "3",
        "rank_snapshot_count": "24",
        "latest_snapshot_at": datetime(2026, 7, 7, 12, 30),
        "avg_total_score": Decimal("72.5"),
        "avg_organic_rank": Decimal("8.2"),
        "source_types": "amazon_suggest,title_ngram,amazon_suggest",
        "idea_statuses": "promoted",
        "idea_count": "1",
        "active_count": "1",
        "tracking_task_count": "1",
        "active_tracking_task_id": "9",
    }

    result = _normalize_asset_row(row)

    assert result["keyword_id"] == 12
    assert result["product_count"] == 8
    assert result["latest_snapshot_at"] == "2026-07-07 12:30:00"
    assert result["avg_total_score"] == 72.5
    assert result["source_types"] == ["amazon_suggest", "title_ngram"]
    assert result["has_snapshots"] is True
    assert result["has_workshop_idea"] is True
    assert result["tracking_status"] == "active"
    assert result["tracking_task_id"] == 9


def test_build_asset_filters_uses_safe_known_filter_values() -> None:
    where, params = _build_asset_filters(
        marketplace="us",
        keyword="Squishy",
        snapshot_filter="without",
        tracking_filter="none",
        source_filter="workshop",
    )

    assert "k.marketplace = %s" in where
    assert "LOWER(k.keyword) LIKE %s" in where
    assert "snapshot_time_count, 0) = 0" in where
    assert "tracking_task_count, 0) = 0" in where
    assert "idea_stats.idea_count, 0) > 0" in where
    assert params == ["US", "%squishy%"]


def test_create_tracking_requires_keyword_selection() -> None:
    try:
        create_tracking_for_keywords([])
    except ValueError as exc:
        assert "请选择" in str(exc)
    else:
        raise AssertionError("empty keyword ids should fail")


def _find_node(nodes: list[dict], keyword: str) -> dict:
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if node["keyword"] == keyword:
            return node
        stack.extend(node.get("children") or [])
    raise AssertionError(f"missing keyword node: {keyword}")


def test_build_keyword_tree_infers_longest_parent_keyword() -> None:
    rows = [
        {"keyword_id": 1, "keyword": "Squishy", "product_count": 10},
        {"keyword_id": 2, "keyword": "Cow Squishy", "product_count": 8},
        {"keyword_id": 3, "keyword": "Mini Cow Squishy", "product_count": 4},
        {"keyword_id": 4, "keyword": "Christmas Cow Squishy", "product_count": 3},
        {"keyword_id": 5, "keyword": "Axolotl Squishy", "product_count": 2},
        {"keyword_id": 6, "keyword": "Fidget Toys", "product_count": 9},
        {"keyword_id": 7, "keyword": "Fidget Toys for Adults", "product_count": 5},
        {"keyword_id": 8, "keyword": "Anxiety Fidget Toys", "product_count": 5},
    ]

    roots = _build_keyword_tree(rows)

    squishy = _find_node(roots, "Squishy")
    cow = _find_node(roots, "Cow Squishy")
    mini_cow = _find_node(roots, "Mini Cow Squishy")
    fidget = _find_node(roots, "Fidget Toys")
    adults = _find_node(roots, "Fidget Toys for Adults")
    anxiety = _find_node(roots, "Anxiety Fidget Toys")

    assert squishy["parent_keyword_id"] is None
    assert cow["parent_keyword"] == "Squishy"
    assert mini_cow["parent_keyword"] == "Cow Squishy"
    assert _find_node(roots, "Christmas Cow Squishy")["parent_keyword"] == "Cow Squishy"
    assert _find_node(roots, "Axolotl Squishy")["parent_keyword"] == "Squishy"
    assert fidget["parent_keyword_id"] is None
    assert adults["parent_keyword"] == "Fidget Toys"
    assert anxiety["parent_keyword"] == "Fidget Toys"
    assert squishy["descendant_count"] == 4
    assert cow["child_count"] == 2
    assert mini_cow["depth"] == 3


def test_build_keyword_tree_adds_virtual_category_roots_for_shared_terms() -> None:
    rows = [
        {"keyword_id": 1, "keyword": "Axolotl Squishy", "product_count": 2},
        {"keyword_id": 2, "keyword": "Mochi Squishy", "product_count": 3},
        {"keyword_id": 3, "keyword": "Slow Rising Squishy", "product_count": 4},
        {"keyword_id": 4, "keyword": "Fidget Toys for Adults", "product_count": 5},
        {"keyword_id": 5, "keyword": "Anxiety Fidget Toys", "product_count": 6},
    ]

    roots = _build_keyword_tree(rows)

    squishy = _find_node(roots, "Squishy")
    fidget = _find_node(roots, "Fidget Toys")

    assert squishy["virtual"] is True
    assert squishy["keyword_id"] < 0
    assert squishy["child_count"] == 3
    assert _find_node(roots, "Axolotl Squishy")["parent_keyword"] == "Squishy"
    assert fidget["virtual"] is True
    assert fidget["child_count"] == 2
    assert _find_node(roots, "Fidget Toys for Adults")["parent_keyword"] == "Fidget Toys"


if __name__ == "__main__":
    tests = [
        test_normalize_asset_row_adds_booleans_and_tracking_status,
        test_build_asset_filters_uses_safe_known_filter_values,
        test_create_tracking_requires_keyword_selection,
        test_build_keyword_tree_infers_longest_parent_keyword,
        test_build_keyword_tree_adds_virtual_category_roots_for_shared_terms,
    ]
    for test in tests:
        test()
    print(f"keyword_library tests passed: {len(tests)}/{len(tests)}")
