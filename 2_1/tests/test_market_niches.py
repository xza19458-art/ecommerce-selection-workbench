from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.market_niches import (
    MarketNicheError,
    _aggregate_niche_evidence,
    _normalize_asins,
    ensure_research_projects_mutable,
    evidence_level,
    niche_snapshot_source_timing,
    normalize_niche_name,
    remove_niche_project,
)


class _FrozenRelationCursor:
    rowcount = 0

    def __init__(self) -> None:
        self.last_sql = ""
        self.executed_sql: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, sql, params=None) -> None:
        self.last_sql = " ".join(str(sql).split())
        self.executed_sql.append(self.last_sql)

    def fetchone(self):
        if "FROM market_niches" in self.last_sql:
            return {"id": 7, "marketplace": "US"}
        if "FROM research_projects" in self.last_sql:
            return {"id": 2, "status": "approved"}
        return None


class _FrozenRelationConnection:
    def __init__(self, cursor: _FrozenRelationCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self):
        return self._cursor


class _FrozenRelationClient:
    def __init__(self) -> None:
        self.cursor = _FrozenRelationCursor()

    def connect(self):
        return _FrozenRelationConnection(self.cursor)


def _sample_evidence(*, second_keyword_at: str = "2026-07-13 11:00:00"):
    rank_rows = [
        {"id": 1, "keyword_id": 1, "product_id": 10, "snapshot_at": "2026-07-13 10:00:00"},
        {"id": 2, "keyword_id": 2, "product_id": 10, "snapshot_at": second_keyword_at},
        {"id": 3, "keyword_id": 1, "product_id": 11, "snapshot_at": "2026-07-13 10:00:00"},
        {"id": 4, "keyword_id": 2, "product_id": 11, "snapshot_at": second_keyword_at},
        {"id": 5, "keyword_id": 1, "product_id": 12, "snapshot_at": "2026-07-13 10:00:00"},
        {"id": 6, "keyword_id": 2, "product_id": 13, "snapshot_at": second_keyword_at},
        {"id": 7, "keyword_id": 2, "product_id": 14, "snapshot_at": second_keyword_at},
    ]
    serp_rows = [
        {
            "id": 21, "keyword_id": 1, "snapshot_at": "2026-07-13 10:00:00",
            "page_count": 2, "total_card_count": 100, "sponsored_count": 20,
            "unique_asin_count": 60, "data_coverage": 0.8,
        },
        {
            "id": 22, "keyword_id": 2, "snapshot_at": "2026-07-13 11:00:00",
            "page_count": 1, "total_card_count": 50, "sponsored_count": 5,
            "unique_asin_count": 40, "data_coverage": 0.6,
        },
    ]
    product_rows = [
        {
            "product_id": product_id,
            "snapshot_id": 100 + product_id,
            "snapshot_at": "2026-07-13 11:30:00",
            "price": price,
            "review_count": reviews,
            "rating": rating,
            "monthly_bought": monthly,
            "brand": brand,
        }
        for product_id, price, reviews, rating, monthly, brand in [
            (10, 10, 100, 4.1, 100, "A"),
            (11, 20, 200, 4.2, 200, "A"),
            (12, 30, 300, 4.3, 300, "B"),
            (13, 40, 400, 4.4, None, "C"),
            (14, 50, 500, 4.5, 400, "D"),
        ]
    ]
    return _aggregate_niche_evidence(
        keyword_ids=[1, 2],
        rank_rows=rank_rows,
        serp_rows=serp_rows,
        product_rows=product_rows,
        manual_product_ids=[99],
    )


def test_niche_name_normalization_is_stable() -> None:
    assert normalize_niche_name("  Squishy   Animals ") == "squishy animals"
    assert normalize_niche_name("SQUISHY animals") == "squishy animals"


def test_invalid_asin_is_rejected() -> None:
    try:
        _normalize_asins(["short"])
    except MarketNicheError as exc:
        assert "ASIN 格式" in str(exc)
    else:
        raise AssertionError("invalid ASIN should be rejected")


def test_terminal_research_project_cannot_change_niche_relation() -> None:
    ensure_research_projects_mutable([{"id": 1, "status": "manual_review"}])
    try:
        ensure_research_projects_mutable(
            [{"id": 2, "status": "approved"}, {"id": 3, "status": "idea"}]
        )
    except MarketNicheError as exc:
        assert "#2" in str(exc)
        assert "已冻结" in str(exc)
    else:
        raise AssertionError("approved project should reject niche relation changes")


def test_remove_niche_project_blocks_delete_for_terminal_project() -> None:
    client = _FrozenRelationClient()
    try:
        remove_niche_project(7, 2, client=client)
    except MarketNicheError as exc:
        assert "#2" in str(exc)
    else:
        raise AssertionError("terminal project relation deletion should be blocked")
    assert not any(sql.startswith("DELETE ") for sql in client.cursor.executed_sql)


def test_market_sample_deduplicates_products_across_keywords() -> None:
    metrics = _sample_evidence()
    assert metrics["observed_product_count"] == 5
    assert metrics["repeated_product_count"] == 2
    assert metrics["cross_keyword_overlap"] == 0.4
    assert metrics["manual_product_count"] == 1


def test_market_distributions_and_coverages_are_explainable() -> None:
    metrics = _sample_evidence()
    assert metrics["rank_coverage"] == 1.0
    assert metrics["serp_coverage"] == 1.0
    assert metrics["serp_data_coverage"] == 0.72
    assert metrics["ad_density"] == 0.1667
    assert metrics["price_median"] == 30.0
    assert metrics["review_median"] == 300.0
    assert metrics["monthly_bought_median"] == 250.0
    assert metrics["monthly_bought_total"] == 1000.0
    assert metrics["monthly_bought_coverage"] == 0.8
    assert metrics["demand_cr3"] == 0.9
    assert metrics["brand_product_cr3"] == 0.8


def test_evidence_hash_is_stable_and_warns_about_small_samples() -> None:
    first = _sample_evidence()
    second = _sample_evidence()
    assert first["evidence_hash"] == second["evidence_hash"]
    identity = first["raw_json"]["source_identity"]
    assert identity["rank_basis"] == "latest_keyword_batch_v2"
    assert identity["rank_batch_times"] == [
        [1, "2026-07-13 10:00:00"],
        [2, "2026-07-13 11:00:00"],
    ]
    assert first["raw_json"]["aggregation_version"] == "latest_keyword_batch_v2"
    assert first["rank_source_alignment"] == "adjacent"
    assert first["rank_source_span_hours"] == 1.0
    assert first["raw_json"]["rank_source_time_range"]["member_batch_count"] == 2
    assert not any("混合时点" in warning for warning in first["raw_json"]["warnings"])
    assert any("少于 20" in warning for warning in first["raw_json"]["warnings"])
    assert evidence_level(first) == "insufficient"


def test_rank_source_time_range_warns_when_member_batches_are_far_apart() -> None:
    metrics = _sample_evidence(second_keyword_at="2026-07-21 10:00:00")
    timing = niche_snapshot_source_timing(metrics["raw_json"])

    assert metrics["rank_source_first_at"] == "2026-07-13 10:00:00"
    assert metrics["rank_source_latest_at"] == "2026-07-21 10:00:00"
    assert metrics["rank_source_span_hours"] == 192.0
    assert metrics["rank_source_span_days"] == 8.0
    assert metrics["rank_source_alignment"] == "mixed_period"
    assert timing == metrics["raw_json"]["rank_source_time_range"]
    assert any("混合时点" in warning for warning in metrics["raw_json"]["warnings"])


def test_old_snapshot_identity_can_derive_rank_source_timing() -> None:
    timing = niche_snapshot_source_timing(
        {
            "source_identity": {
                "rank_batch_times": [
                    [1, "2026-07-13 10:00:00"],
                    [2, "2026-07-14 10:00:00"],
                ]
            }
        }
    )

    assert timing["alignment"] == "adjacent"
    assert timing["span_hours"] == 24.0
    assert timing["member_batch_count"] == 2


def test_snapshot_query_uses_latest_complete_keyword_batch() -> None:
    service = (ROOT / "services" / "market_niches.py").read_text(encoding="utf-8")
    assert "GROUP BY keyword_id, product_id" not in service
    assert "GROUP BY keyword_id" in service
    assert '"rank_basis": "latest_keyword_batch_v2"' in service


def test_evidence_level_does_not_become_opportunity_score() -> None:
    snapshot = {
        "keyword_count": 3,
        "keyword_with_rank_count": 3,
        "observed_product_count": 120,
        "rank_coverage": 1.0,
        "serp_coverage": 0.67,
        "serp_data_coverage": 0.8,
        "product_snapshot_coverage": 0.9,
        "monthly_bought_coverage": 0.7,
    }
    assert evidence_level(snapshot) == "usable"
    snapshot["serp_coverage"] = 0.33
    assert evidence_level(snapshot) == "partial"


def test_migration_contains_relation_and_snapshot_deduplication() -> None:
    sql = (ROOT / "database" / "migrations" / "20260713_market_niche_v1.sql").read_text(encoding="utf-8")
    assert "uk_market_niche_market_name (marketplace, normalized_name)" in sql
    assert "uk_niche_keyword (niche_id, keyword_id)" in sql
    assert "uk_niche_product (niche_id, product_id)" in sql
    assert "uk_research_project_niche (project_id, niche_id)" in sql
    assert "uk_niche_snapshot_evidence (niche_id, evidence_hash)" in sql
    schema = (ROOT / "database" / "schema.sql").read_text(encoding="utf-8")
    assert schema.index("CREATE TABLE IF NOT EXISTS keywords (") < schema.index(
        "CREATE TABLE IF NOT EXISTS keyword_serp_snapshots ("
    )
    spec = (ROOT / "AmazonSelectionWorkbench.spec").read_text(encoding="utf-8")
    assert '("database/schema.sql", "database")' in spec
    assert '("database/migrations", "database/migrations")' in spec


if __name__ == "__main__":
    tests = [
        test_niche_name_normalization_is_stable,
        test_invalid_asin_is_rejected,
        test_terminal_research_project_cannot_change_niche_relation,
        test_remove_niche_project_blocks_delete_for_terminal_project,
        test_market_sample_deduplicates_products_across_keywords,
        test_market_distributions_and_coverages_are_explainable,
        test_evidence_hash_is_stable_and_warns_about_small_samples,
        test_rank_source_time_range_warns_when_member_batches_are_far_apart,
        test_old_snapshot_identity_can_derive_rank_source_timing,
        test_snapshot_query_uses_latest_complete_keyword_batch,
        test_evidence_level_does_not_become_opportunity_score,
        test_migration_contains_relation_and_snapshot_deduplication,
    ]
    for test in tests:
        test()
    print(f"market niche tests passed: {len(tests)}/{len(tests)}")
