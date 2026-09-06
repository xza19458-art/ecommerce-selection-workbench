"""Repository boundary for the MySQL-to-DuckDB/Parquet analytical copy."""

from __future__ import annotations

from typing import Any

from services.analytics_warehouse import get_warehouse_status, sync_analytics_warehouse


class WarehouseRepository:
    def get_status(self) -> dict[str, Any]:
        return get_warehouse_status()

    def sync(self) -> dict[str, Any]:
        summary = sync_analytics_warehouse()
        return {
            "总行数": summary.total_rows,
            "DuckDB": str(summary.duckdb_path),
            "Parquet": str(summary.parquet_dir),
            "清单": str(summary.manifest_path),
            "同步时间": summary.synced_at,
            "同步表": {table.name: table.rows for table in summary.tables},
        }
