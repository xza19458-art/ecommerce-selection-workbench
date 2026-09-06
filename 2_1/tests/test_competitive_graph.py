from __future__ import annotations

import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.competitive_graph import (  # noqa: E402
    _source_integrity,
    build_competitive_graph_data,
    organic_visibility_proxy,
    relationship_confidence,
)


def _graph(*, focus_asin: str | None = "B000000010") -> dict:
    keywords = [
        {"keyword_id": 1, "keyword": "cow squishy"},
        {"keyword_id": 2, "keyword": "mini squishy"},
        {"keyword_id": 3, "keyword": "axolotl squishy"},
    ]
    ranks = [
        {"id": 1, "keyword_id": 1, "product_id": 10, "organic_rank": 2, "is_sponsored": 0, "page_no": 1},
        {"id": 2, "keyword_id": 1, "product_id": 11, "organic_rank": 10, "is_sponsored": 0, "page_no": 1},
        {"id": 3, "keyword_id": 2, "product_id": 10, "organic_rank": None, "is_sponsored": 1, "page_no": 1},
        {"id": 4, "keyword_id": 2, "product_id": 11, "organic_rank": 5, "is_sponsored": 0, "page_no": 1},
        {"id": 5, "keyword_id": 2, "product_id": 12, "organic_rank": 20, "is_sponsored": 0, "page_no": 1},
        {"id": 6, "keyword_id": 3, "product_id": 12, "organic_rank": 4, "is_sponsored": 0, "page_no": 1},
        # 异常重复边不能覆盖同一关键词下更可信的自然位次。
        {"id": 99, "keyword_id": 1, "product_id": 10, "organic_rank": None, "is_sponsored": 1, "page_no": 1},
    ]
    products = [
        {"product_id": 10, "marketplace": "US", "asin": "B000000010", "title": "Cow", "monthly_bought": 100},
        {"product_id": 11, "marketplace": "US", "asin": "B000000011", "title": "Mini Cow", "monthly_bought": 200},
        {"product_id": 12, "marketplace": "US", "asin": "B000000012", "title": "Axolotl", "monthly_bought": 300},
    ]
    return build_competitive_graph_data(
        niche={"id": 7, "name": "Squishy Animals", "marketplace": "US"},
        snapshot={"id": 70, "snapshot_at": "2026-07-13 12:00:00"},
        available_snapshots=[{"id": 70, "snapshot_at": "2026-07-13 12:00:00"}],
        keyword_rows=keywords,
        rank_rows=ranks,
        product_rows=products,
        source_integrity={"complete": True, "warnings": [], "expected": {}, "loaded": {}, "missing": {}},
        limit=100,
        min_shared=1,
        focus_asin=focus_asin,
    )


def test_keyword_cooccurrence_uses_jaccard_and_containment() -> None:
    graph = _graph()
    relation = next(
        row for row in graph["keyword_relations"]
        if {row["left_keyword_id"], row["right_keyword_id"]} == {1, 2}
    )
    assert relation["shared_product_count"] == 2
    assert relation["union_product_count"] == 3
    assert relation["jaccard"] == 0.6667
    assert {relation["left_containment"], relation["right_containment"]} == {1.0, 0.6667}
    assert relation["shared_organic_product_count"] == 1


def test_duplicate_edge_prefers_observed_organic_rank() -> None:
    graph = _graph()
    product = next(row for row in graph["competitors"] if row["product_id"] == 10)
    cow = next(row for row in product["keyword_observations"] if row["keyword_id"] == 1)
    assert graph["summary"]["rank_edge_count"] == 6
    assert cow["status"] == "organic"
    assert cow["organic_rank"] == 2


def test_visibility_proxy_counts_missing_and_sponsored_as_zero() -> None:
    expected = round((1 / math.log2(3)) / 3, 4)
    assert organic_visibility_proxy([2], 3) == expected
    graph = _graph()
    product = next(row for row in graph["competitors"] if row["product_id"] == 10)
    assert product["organic_visibility_proxy"] == expected
    assert product["organic_keyword_count"] == 1
    assert product["sponsored_keyword_count"] == 1


def test_focus_asin_gap_never_claims_absence_outside_collected_scope() -> None:
    graph = _graph()
    focus = graph["focus"]
    statuses = {row["keyword_id"]: row for row in focus["keyword_gaps"]}
    assert statuses[1]["status_label"] == "观察到自然位次"
    assert statuses[2]["status_label"] == "仅观察到广告"
    assert statuses[3]["status_label"] == "未在已采集范围观察到"
    assert focus["summary"] == {
        "keyword_count": 3,
        "organic_count": 1,
        "sponsored_count": 1,
        "unobserved_count": 1,
    }


def test_unobserved_focus_asin_gets_all_scoped_gaps() -> None:
    graph = _graph(focus_asin="B000000099")
    assert graph["focus"]["observed_in_snapshot"] is False
    assert graph["focus"]["summary"]["unobserved_count"] == 3
    assert all(row["status"] == "unobserved" for row in graph["focus"]["keyword_gaps"])


def test_relationship_confidence_is_sample_strength_not_score() -> None:
    assert relationship_confidence(20, 50, 60)["code"] == "high"
    assert relationship_confidence(5, 12, 15, 22)["code"] == "medium"
    assert relationship_confidence(4, 100, 100)["code"] == "low"
    graph = _graph()
    assert all("score" not in row for row in graph["keyword_relations"])


def test_missing_frozen_sources_are_explicit() -> None:
    integrity = _source_integrity(
        identity_present=True,
        expected_keyword_ids=[1, 2],
        keyword_rows=[{"keyword_id": 1}],
        expected_rank_ids=[10, 11],
        rank_rows=[{"id": 10}],
        expected_serp_ids=[20],
        serp_rows=[],
        expected_product_snapshot_ids=[30],
        product_snapshot_rows=[],
        observed_product_ids=[40],
        product_rows=[],
    )
    assert integrity["complete"] is False
    assert integrity["missing"]["keyword_ids"] == [2]
    assert integrity["missing"]["rank_snapshot_ids"] == [11]
    assert len(integrity["warnings"]) == 5


def test_p6_does_not_duplicate_graph_edges_in_schema() -> None:
    service = (ROOT / "services" / "competitive_graph.py").read_text(encoding="utf-8")
    decision = (ROOT.parent / "decisions" / "2026-07-13-竞品与关键词图谱V1.md").read_text(encoding="utf-8")
    assert "INSERT INTO" not in service
    assert "不新增数据库表" in decision
    assert "keyword_rank_snapshots" in decision


if __name__ == "__main__":
    tests = [
        test_keyword_cooccurrence_uses_jaccard_and_containment,
        test_duplicate_edge_prefers_observed_organic_rank,
        test_visibility_proxy_counts_missing_and_sponsored_as_zero,
        test_focus_asin_gap_never_claims_absence_outside_collected_scope,
        test_unobserved_focus_asin_gets_all_scoped_gaps,
        test_relationship_confidence_is_sample_strength_not_score,
        test_missing_frozen_sources_are_explicit,
        test_p6_does_not_duplicate_graph_edges_in_schema,
    ]
    for test in tests:
        test()
    print(f"competitive graph tests passed: {len(tests)}/{len(tests)}")
