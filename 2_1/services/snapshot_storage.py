"""Storage helpers for recurring snapshot ingestion and warehouse sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from database.mysql_client import MySQLClient
from services.analytics_warehouse import WarehouseSyncSummary, sync_analytics_warehouse
from services.ingestion import IngestionSummary, ingest_html_files_to_mysql


SNAPSHOT_WAREHOUSE_TABLES = (
    "dim_products",
    "dim_keywords",
    "fact_product_snapshots",
    "fact_keyword_rank_snapshots",
    "fact_product_scores",
)

SNAPSHOT_TABLES = ("product_snapshots", "keyword_rank_snapshots")

EXPECTED_INDEXES = {
    "product_snapshots": {
        "uk_product_snapshot_time": ("product_id", "snapshot_at"),
        "idx_snapshot_at": ("snapshot_at",),
    },
    "keyword_rank_snapshots": {
        "uk_keyword_product_snapshot": ("keyword_id", "product_id", "snapshot_at"),
        "idx_keyword_snapshot": ("keyword_id", "snapshot_at"),
    },
}


@dataclass(frozen=True)
class SnapshotIngestSyncSummary:
    ingestion: IngestionSummary
    warehouse: WarehouseSyncSummary
    snapshot_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_at": self.snapshot_at.isoformat(sep=" ") if self.snapshot_at else None,
            "mysql": {
                "total_found": self.ingestion.total_found,
                "total_valid": self.ingestion.total_valid,
                "total_rejected": self.ingestion.total_rejected,
                "total_inserted": self.ingestion.total_inserted,
                "rejected_reasons": self.ingestion.rejected_reasons,
            },
            "warehouse": {
                "duckdb_path": str(self.warehouse.duckdb_path),
                "parquet_dir": str(self.warehouse.parquet_dir),
                "total_rows": self.warehouse.total_rows,
                "tables": {table.name: table.rows for table in self.warehouse.tables},
            },
        }


def ingest_snapshot_html_and_sync_warehouse(
    html_files: Iterable[str | Path],
    *,
    keyword: str | None = None,
    marketplace: str = "US",
    snapshot_at: datetime | None = None,
    url: str | None = None,
    pages: int | None = None,
    require_complete: bool = True,
    warehouse_tables: Sequence[str] = SNAPSHOT_WAREHOUSE_TABLES,
    client: MySQLClient | None = None,
) -> SnapshotIngestSyncSummary:
    """Import new snapshot HTML into MySQL, then refresh analysis warehouse tables."""

    db = client or MySQLClient()
    ingestion = ingest_html_files_to_mysql(
        html_files,
        keyword=keyword,
        marketplace=marketplace,
        snapshot_at=snapshot_at,
        url=url,
        pages=pages,
        client=db,
        require_complete=require_complete,
    )
    warehouse = sync_analytics_warehouse(client=db, tables=warehouse_tables)
    return SnapshotIngestSyncSummary(
        ingestion=ingestion,
        warehouse=warehouse,
        snapshot_at=snapshot_at,
    )


def evaluate_snapshot_storage(
    *,
    collection_interval_days: float = 3.0,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    """Return read-only capacity and index diagnostics for snapshot tables."""

    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            table_sizes = _fetch_table_sizes(cursor, db.config.database)
            indexes = {table: _fetch_indexes(cursor, table) for table in SNAPSHOT_TABLES}
            snapshot_stats = _fetch_product_snapshot_stats(cursor)
            rank_stats = _fetch_keyword_rank_stats(cursor)

    product_rows_per_year = _project_rows(
        base_count=snapshot_stats.get("products") or 0,
        collection_interval_days=collection_interval_days,
    )
    rank_rows_per_year = _project_rows(
        base_count=rank_stats.get("keyword_products") or rank_stats.get("row_count") or 0,
        collection_interval_days=collection_interval_days,
    )

    return {
        "collection_interval_days": collection_interval_days,
        "tables": table_sizes,
        "indexes": indexes,
        "stats": {
            "product_snapshots": snapshot_stats,
            "keyword_rank_snapshots": rank_stats,
        },
        "projection": {
            "product_snapshots_rows_per_year": product_rows_per_year,
            "keyword_rank_snapshots_rows_per_year": rank_rows_per_year,
            "product_snapshots_estimated_mb_per_year": _project_mb(
                table_sizes.get("product_snapshots", {}),
                current_rows=snapshot_stats.get("row_count") or 0,
                projected_rows=product_rows_per_year,
            ),
            "keyword_rank_snapshots_estimated_mb_per_year": _project_mb(
                table_sizes.get("keyword_rank_snapshots", {}),
                current_rows=rank_stats.get("row_count") or 0,
                projected_rows=rank_rows_per_year,
            ),
        },
        "recommendations": _build_storage_recommendations(table_sizes, indexes, snapshot_stats, rank_stats),
    }


def _fetch_table_sizes(cursor: Any, database: str) -> dict[str, dict[str, Any]]:
    placeholders = ", ".join(["%s"] * len(SNAPSHOT_TABLES))
    cursor.execute(
        f"""
        SELECT
          table_name,
          table_rows,
          data_length,
          index_length,
          ROUND((data_length + index_length) / 1024 / 1024, 4) AS total_mb
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name IN ({placeholders})
        """,
        [database, *SNAPSHOT_TABLES],
    )
    return {
        _row_value(row, "table_name", "TABLE_NAME"): {
            "estimated_rows": int(_row_value(row, "table_rows", "TABLE_ROWS") or 0),
            "data_bytes": int(_row_value(row, "data_length", "DATA_LENGTH") or 0),
            "index_bytes": int(_row_value(row, "index_length", "INDEX_LENGTH") or 0),
            "total_mb": float(_row_value(row, "total_mb", "TOTAL_MB") or 0.0),
        }
        for row in cursor.fetchall()
    }


def _fetch_indexes(cursor: Any, table: str) -> dict[str, dict[str, Any]]:
    cursor.execute(f"SHOW INDEX FROM {table}")
    indexes: dict[str, dict[str, Any]] = {}
    for row in cursor.fetchall():
        name = _row_value(row, "Key_name", "KEY_NAME")
        entry = indexes.setdefault(
            name,
            {
                "unique": int(_row_value(row, "Non_unique", "NON_UNIQUE") or 0) == 0,
                "columns": [],
            },
        )
        entry["columns"].append(
            (
                int(_row_value(row, "Seq_in_index", "SEQ_IN_INDEX")),
                _row_value(row, "Column_name", "COLUMN_NAME"),
            )
        )
    for entry in indexes.values():
        entry["columns"] = [column for _, column in sorted(entry["columns"])]
    return indexes


def _fetch_product_snapshot_stats(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
          COUNT(*) AS row_count,
          COUNT(DISTINCT product_id) AS products,
          COUNT(DISTINCT snapshot_at) AS snapshot_times,
          MIN(snapshot_at) AS min_snapshot_at,
          MAX(snapshot_at) AS max_snapshot_at
        FROM product_snapshots
        """
    )
    stats = dict(cursor.fetchone())
    cursor.execute(
        """
        SELECT
          MIN(c) AS min_snapshots_per_product,
          AVG(c) AS avg_snapshots_per_product,
          MAX(c) AS max_snapshots_per_product
        FROM (
          SELECT product_id, COUNT(*) AS c
          FROM product_snapshots
          GROUP BY product_id
        ) t
        """
    )
    stats.update(dict(cursor.fetchone() or {}))
    return _normalize_stats(stats)


def _fetch_keyword_rank_stats(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
          COUNT(*) AS row_count,
          COUNT(DISTINCT keyword_id) AS keywords,
          COUNT(DISTINCT product_id) AS products,
          COUNT(DISTINCT CONCAT(keyword_id, ':', product_id)) AS keyword_products,
          COUNT(DISTINCT snapshot_at) AS snapshot_times,
          MIN(snapshot_at) AS min_snapshot_at,
          MAX(snapshot_at) AS max_snapshot_at
        FROM keyword_rank_snapshots
        """
    )
    return _normalize_stats(dict(cursor.fetchone()))


def _normalize_stats(stats: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in stats.items():
        if isinstance(value, datetime):
            normalized[key] = value.isoformat(sep=" ")
        elif value is None:
            normalized[key] = None
        elif key.startswith("avg_"):
            normalized[key] = round(float(value), 4)
        elif isinstance(value, float):
            normalized[key] = round(value, 4)
        else:
            try:
                normalized[key] = int(value)
            except (TypeError, ValueError):
                normalized[key] = value
    return normalized


def _project_rows(*, base_count: int, collection_interval_days: float) -> int:
    if base_count <= 0 or collection_interval_days <= 0:
        return 0
    return int(round(base_count * (365.0 / collection_interval_days)))


def _project_mb(table_size: dict[str, Any], *, current_rows: int, projected_rows: int) -> float | None:
    if current_rows <= 0 or projected_rows <= 0:
        return None
    total_bytes = int(table_size.get("data_bytes") or 0) + int(table_size.get("index_bytes") or 0)
    if total_bytes <= 0:
        return None
    return round((total_bytes / current_rows * projected_rows) / 1024 / 1024, 4)


def _build_storage_recommendations(
    table_sizes: dict[str, dict[str, Any]],
    indexes: dict[str, dict[str, dict[str, Any]]],
    snapshot_stats: dict[str, Any],
    rank_stats: dict[str, Any],
) -> list[str]:
    recommendations: list[str] = []
    for table, expected in EXPECTED_INDEXES.items():
        existing = indexes.get(table, {})
        for index_name, columns in expected.items():
            if existing.get(index_name, {}).get("columns") != list(columns):
                recommendations.append(f"{table} 缺少或不匹配索引 {index_name}({', '.join(columns)})")

    if not recommendations:
        recommendations.append("当前快照核心索引完整，B2 阶段暂不需要 schema 迁移。")

    avg_snapshots = float(snapshot_stats.get("avg_snapshots_per_product") or 0)
    if avg_snapshots < 3:
        recommendations.append("单品平均快照数仍低于 3，趋势分析应继续标记为样本不足/低置信度。")

    if (snapshot_stats.get("row_count") or 0) < 100_000 and (rank_stats.get("row_count") or 0) < 100_000:
        recommendations.append("当前数据量远低于需要分表/归档的阈值，可继续使用现有 MySQL 表 + Parquet 同步。")

    for table in SNAPSHOT_TABLES:
        total_mb = (table_sizes.get(table) or {}).get("total_mb")
        if total_mb is not None and total_mb > 512:
            recommendations.append(f"{table} 已超过 512MB，建议评估按月归档到 Parquet 分区。")

    return recommendations


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    lower_map = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        lowered = key.lower()
        if lowered in lower_map:
            return lower_map[lowered]
    raise KeyError(keys[0])
