from services.snapshot_storage import SNAPSHOT_WAREHOUSE_TABLES


def test_snapshot_sync_includes_every_search_derived_fact() -> None:
    assert {
        "dim_products",
        "dim_keywords",
        "fact_product_snapshots",
        "fact_keyword_rank_snapshots",
        "fact_keyword_serp_snapshots",
        "fact_product_scores",
    } <= set(SNAPSHOT_WAREHOUSE_TABLES)
