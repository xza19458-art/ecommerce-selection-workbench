from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.analytics_warehouse import (
    WarehouseConfig,
    _import_duckdb,
    _sql_literal,
    query_warehouse,
)


def test_query_warehouse_runtime_views_ignore_stale_persistent_paths() -> None:
    duckdb = _import_duckdb()
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        stale_dir = tmp_path / "stale"
        current_parquet_dir = tmp_path / "current" / "parquet"
        stale_dir.mkdir(parents=True)
        current_parquet_dir.mkdir(parents=True)

        duckdb_path = tmp_path / "warehouse.duckdb"
        current_parquet = current_parquet_dir / "fact_product_snapshots.parquet"
        stale_parquet = stale_dir / "fact_product_snapshots.parquet"

        with duckdb.connect(str(duckdb_path)) as conn:
            conn.execute(
                f"""
                COPY (
                  SELECT
                    'BTEST12345' AS asin,
                    CAST(19.99 AS DOUBLE) AS price
                )
                TO {_sql_literal(current_parquet)}
                (FORMAT PARQUET)
                """
            )
            conn.execute(
                f"""
                COPY (
                  SELECT
                    'BSTALE0000' AS asin,
                    CAST(1.00 AS DOUBLE) AS price
                )
                TO {_sql_literal(stale_parquet)}
                (FORMAT PARQUET)
                """
            )
            conn.execute(
                f"""
                CREATE VIEW fact_product_snapshots AS
                SELECT * FROM read_parquet({_sql_literal(stale_parquet)})
                """
            )
        stale_parquet.unlink()

        config = WarehouseConfig(
            root_dir=tmp_path / "current",
            parquet_dir=current_parquet_dir,
            duckdb_path=duckdb_path,
        )
        rows = query_warehouse(
            """
            SELECT asin, price
            FROM fact_product_snapshots
            ORDER BY asin
            """,
            config=config,
        )

    assert rows == [{"asin": "BTEST12345", "price": 19.99}]


if __name__ == "__main__":
    tests = [test_query_warehouse_runtime_views_ignore_stale_persistent_paths]
    for test in tests:
        test()
    print(f"analytics_warehouse path tests passed: {len(tests)}/{len(tests)}")
