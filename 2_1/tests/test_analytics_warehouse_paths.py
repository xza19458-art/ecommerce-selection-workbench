from __future__ import annotations

from pathlib import Path
import json
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.analytics_warehouse import (
    MANIFEST_FILE_NAME,
    WAREHOUSE_SOURCE_TABLES,
    WAREHOUSE_TABLES,
    WarehouseConfig,
    get_warehouse_status,
    _import_duckdb,
    _sql_literal,
    query_warehouse,
)


class _FakeWarehouseCursor:
    def __init__(self, markers: dict[str, dict[str, object]]) -> None:
        self.markers = markers
        self.table_name = ""
        self.mode = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql: str) -> None:
        if sql.startswith("SHOW COLUMNS"):
            match = re.search(r"FROM\s+`([a-z0-9_]+)`", sql, re.IGNORECASE)
            self.mode = "columns"
        else:
            match = re.search(r"FROM\s+`([a-z0-9_]+)`", sql, re.IGNORECASE)
            self.mode = "marker"
        if not match:
            raise AssertionError(f"table identifier missing from SQL: {sql}")
        self.table_name = match.group(1)

    def fetchall(self):
        if self.mode == "columns":
            return [{"Field": "id"}, {"Field": "updated_at"}]
        raise AssertionError("fetchall called for marker query")

    def fetchone(self):
        marker = self.markers[self.table_name]
        return {
            "row_count": marker["rows"],
            "max_id": marker["max_id"],
            "max_marker": marker["max_marker"],
        }


class _FakeWarehouseConnection:
    def __init__(self, markers: dict[str, dict[str, object]]) -> None:
        self.markers = markers

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return _FakeWarehouseCursor(self.markers)


class _FakeWarehouseClient:
    def __init__(self, markers: dict[str, dict[str, object]]) -> None:
        self.markers = markers

    def connect(self):
        return _FakeWarehouseConnection(self.markers)


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


def test_warehouse_status_detects_current_and_stale_sources() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        parquet_dir = root / "parquet"
        parquet_dir.mkdir()
        config = WarehouseConfig(
            root_dir=root,
            parquet_dir=parquet_dir,
            duckdb_path=root / "warehouse.duckdb",
        )
        source_markers = {
            source_table: {
                "rows": 1,
                "max_id": 1,
                "marker_column": "updated_at",
                "max_marker": "2026-07-16 12:00:00",
            }
            for source_table in WAREHOUSE_SOURCE_TABLES.values()
        }
        manifest_tables = {}
        for warehouse_table, source_table in WAREHOUSE_SOURCE_TABLES.items():
            parquet_path = parquet_dir / f"{warehouse_table}.parquet"
            parquet_path.touch()
            manifest_tables[warehouse_table] = {
                "rows": 1,
                "source": source_markers[source_table],
            }
        (root / MANIFEST_FILE_NAME).write_text(
            json.dumps({"synced_at": "2026-07-16 12:00:00", "tables": manifest_tables}),
            encoding="utf-8",
        )

        status = get_warehouse_status(
            config=config,
            client=_FakeWarehouseClient(source_markers),
        )
        assert status["status"] == "current"
        assert not status["stale_tables"]

        changed_markers = {name: dict(marker) for name, marker in source_markers.items()}
        changed_markers["products"]["rows"] = 2
        status = get_warehouse_status(
            config=config,
            client=_FakeWarehouseClient(changed_markers),
        )
        assert status["status"] == "stale"
        assert status["stale_tables"] == ["dim_products"]


def test_warehouse_status_requires_manifest_and_all_parquet_files() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        markers = {
            source_table: {
                "rows": 0,
                "max_id": None,
                "marker_column": "updated_at",
                "max_marker": None,
            }
            for source_table in WAREHOUSE_SOURCE_TABLES.values()
        }
        status = get_warehouse_status(
            config=WarehouseConfig(
                root_dir=root,
                parquet_dir=root / "parquet",
                duckdb_path=root / "warehouse.duckdb",
            ),
            client=_FakeWarehouseClient(markers),
        )
        assert status["status"] == "missing"
        assert status["missing_tables"] == list(WAREHOUSE_TABLES)


if __name__ == "__main__":
    tests = [
        test_query_warehouse_runtime_views_ignore_stale_persistent_paths,
        test_warehouse_status_detects_current_and_stale_sources,
        test_warehouse_status_requires_manifest_and_all_parquet_files,
    ]
    for test in tests:
        test()
    print(f"analytics_warehouse path tests passed: {len(tests)}/{len(tests)}")
