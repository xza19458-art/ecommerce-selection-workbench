"""Local analytics warehouse exports backed by DuckDB and Parquet.

This module is intentionally additive: MySQL remains the operational store,
while immutable or analysis-heavy tables can be exported into a local OLAP
workspace for trend and decision queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from database.mysql_client import MySQLClient


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "warehouse.json"
DEFAULT_ROOT_DIR = ROOT / "data_warehouse"
DEFAULT_PARQUET_DIR = DEFAULT_ROOT_DIR / "parquet"
DEFAULT_DUCKDB_PATH = DEFAULT_ROOT_DIR / "amazon_selection.duckdb"


WAREHOUSE_TABLES: dict[str, str] = {
    "dim_products": """
        SELECT
          p.id AS product_id,
          p.marketplace,
          p.asin,
          p.title,
          p.title_zh,
          p.title_lang,
          p.title_translation_status,
          p.title_translation_engine,
          p.title_translated_at,
          p.brand,
          p.category_path,
          p.product_url,
          p.image_url,
          p.first_seen_at,
          p.last_seen_at,
          p.created_at,
          p.updated_at
        FROM products p
    """,
    "dim_keywords": """
        SELECT
          k.id AS keyword_id,
          k.marketplace,
          k.keyword,
          k.created_at
        FROM keywords k
    """,
    "fact_product_snapshots": """
        SELECT
          s.id AS snapshot_id,
          s.product_id,
          p.marketplace,
          p.asin,
          s.snapshot_at,
          s.price,
          s.rating,
          s.review_count,
          s.monthly_bought,
          s.is_deal,
          s.is_sponsored,
          s.page_no,
          s.organic_rank,
          s.raw_json,
          s.created_at
        FROM product_snapshots s
        JOIN products p ON p.id = s.product_id
    """,
    "fact_keyword_rank_snapshots": """
        SELECT
          r.id AS rank_snapshot_id,
          r.keyword_id,
          k.marketplace,
          k.keyword,
          r.product_id,
          p.asin,
          r.snapshot_at,
          r.page_no,
          r.organic_rank,
          r.is_sponsored,
          r.created_at
        FROM keyword_rank_snapshots r
        JOIN keywords k ON k.id = r.keyword_id
        JOIN products p ON p.id = r.product_id
    """,
    "fact_product_scores": """
        SELECT
          ps.id AS score_id,
          ps.product_id,
          p.marketplace,
          p.asin,
          ps.keyword_id,
          k.keyword,
          ps.score_date,
          ps.total_score,
          ps.demand_score,
          ps.growth_score,
          ps.competition_score,
          ps.rating_score,
          ps.price_score,
          ps.rank_score,
          ps.reason,
          ps.created_at
        FROM product_scores ps
        JOIN products p ON p.id = ps.product_id
        LEFT JOIN keywords k ON k.id = ps.keyword_id
    """,
    "fact_product_reviews": """
        SELECT
          r.id AS review_row_id,
          r.product_id,
          p.marketplace,
          p.asin,
          r.review_id,
          r.content_hash,
          r.rating,
          r.title,
          r.title_zh,
          r.body,
          r.body_zh,
          r.review_lang,
          r.review_translation_status,
          r.review_translation_engine,
          r.review_translated_at,
          r.review_at,
          r.reviewer_name,
          r.verified_purchase,
          r.helpful_votes,
          r.variant_info,
          r.source_url,
          r.raw_json,
          r.collected_at,
          r.created_at
        FROM product_reviews r
        JOIN products p ON p.id = r.product_id
    """,
    "mart_review_insights": """
        SELECT
          i.id AS insight_id,
          i.product_id,
          p.marketplace,
          p.asin,
          i.insight_date,
          i.review_count,
          i.negative_count,
          i.avg_rating,
          i.pain_points_json,
          i.positive_points_json,
          i.opportunity_summary,
          i.risk_summary,
          i.created_at,
          i.updated_at
        FROM product_review_insights i
        JOIN products p ON p.id = i.product_id
    """,
}


@dataclass(frozen=True)
class WarehouseConfig:
    root_dir: Path = DEFAULT_ROOT_DIR
    parquet_dir: Path = DEFAULT_PARQUET_DIR
    duckdb_path: Path = DEFAULT_DUCKDB_PATH

    @classmethod
    def from_file(cls, path: str | Path = CONFIG_PATH) -> "WarehouseConfig":
        config_path = _resolve_project_path(path)
        if not config_path.exists():
            return cls()

        data = json.loads(config_path.read_text(encoding="utf-8"))
        root_dir = _resolve_project_path(data.get("root_dir", DEFAULT_ROOT_DIR))
        parquet_dir = _resolve_project_path(data.get("parquet_dir", root_dir / "parquet"))
        duckdb_path = _resolve_project_path(data.get("duckdb_path", root_dir / "amazon_selection.duckdb"))
        return cls(root_dir=root_dir, parquet_dir=parquet_dir, duckdb_path=duckdb_path)

    def with_overrides(
        self,
        *,
        root_dir: str | Path | None = None,
        parquet_dir: str | Path | None = None,
        duckdb_path: str | Path | None = None,
    ) -> "WarehouseConfig":
        next_root = _resolve_project_path(root_dir) if root_dir is not None else self.root_dir
        next_parquet = _resolve_project_path(parquet_dir) if parquet_dir is not None else (
            next_root / "parquet" if root_dir is not None else self.parquet_dir
        )
        next_duckdb = _resolve_project_path(duckdb_path) if duckdb_path is not None else (
            next_root / "amazon_selection.duckdb" if root_dir is not None else self.duckdb_path
        )
        return WarehouseConfig(root_dir=next_root, parquet_dir=next_parquet, duckdb_path=next_duckdb)


@dataclass(frozen=True)
class WarehouseTableSummary:
    name: str
    rows: int
    parquet_path: Path


@dataclass(frozen=True)
class WarehouseSyncSummary:
    duckdb_path: Path
    parquet_dir: Path
    tables: tuple[WarehouseTableSummary, ...]

    @property
    def total_rows(self) -> int:
        return sum(table.rows for table in self.tables)


def list_warehouse_tables() -> tuple[str, ...]:
    return tuple(WAREHOUSE_TABLES.keys())


def sync_analytics_warehouse(
    *,
    config: WarehouseConfig | None = None,
    client: MySQLClient | None = None,
    tables: Sequence[str] | None = None,
) -> WarehouseSyncSummary:
    selected_tables = _normalize_tables(tables)
    warehouse_config = config or WarehouseConfig.from_file()
    duckdb = _import_duckdb()
    db = client or MySQLClient()

    warehouse_config.root_dir.mkdir(parents=True, exist_ok=True)
    warehouse_config.parquet_dir.mkdir(parents=True, exist_ok=True)
    warehouse_config.duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    summaries: list[WarehouseTableSummary] = []
    with db.connect() as mysql_conn:
        with mysql_conn.cursor() as cursor:
            with duckdb.connect(str(warehouse_config.duckdb_path)) as warehouse_conn:
                for table_name in selected_tables:
                    dataframe = _fetch_dataframe(cursor, WAREHOUSE_TABLES[table_name])
                    dataframe = _normalize_dataframe(dataframe)
                    parquet_path = warehouse_config.parquet_dir / f"{table_name}.parquet"
                    _write_parquet(warehouse_conn, table_name, dataframe, parquet_path)
                    _create_view(warehouse_conn, table_name, parquet_path)
                    summaries.append(
                        WarehouseTableSummary(
                            name=table_name,
                            rows=len(dataframe),
                            parquet_path=parquet_path,
                        )
                    )

    return WarehouseSyncSummary(
        duckdb_path=warehouse_config.duckdb_path,
        parquet_dir=warehouse_config.parquet_dir,
        tables=tuple(summaries),
    )


def refresh_warehouse_views(
    *,
    config: WarehouseConfig | None = None,
    tables: Sequence[str] | None = None,
) -> None:
    selected_tables = _normalize_tables(tables)
    warehouse_config = config or WarehouseConfig.from_file()
    duckdb = _import_duckdb()

    warehouse_config.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(warehouse_config.duckdb_path)) as conn:
        for table_name in selected_tables:
            parquet_path = warehouse_config.parquet_dir / f"{table_name}.parquet"
            if parquet_path.exists():
                _create_view(conn, table_name, parquet_path)


def query_warehouse(
    sql: str,
    params: Sequence[Any] | None = None,
    *,
    config: WarehouseConfig | None = None,
) -> list[dict[str, Any]]:
    warehouse_config = config or WarehouseConfig.from_file()
    duckdb = _import_duckdb()

    with duckdb.connect(str(warehouse_config.duckdb_path), read_only=True) as conn:
        _create_runtime_views(conn, warehouse_config)
        result = conn.execute(sql, params or [])
        columns = [description[0] for description in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]


def _fetch_dataframe(cursor: Any, sql: str) -> pd.DataFrame:
    cursor.execute(sql)
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description or []]
    return pd.DataFrame(rows, columns=columns)


def _normalize_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe

    normalized = dataframe.copy()
    for column in normalized.columns:
        if normalized[column].dtype == "object":
            normalized[column] = normalized[column].map(_normalize_value)
    return normalized


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _write_parquet(conn: Any, table_name: str, dataframe: pd.DataFrame, parquet_path: Path) -> None:
    temp_view = f"export_{table_name}"
    conn.register(temp_view, dataframe)
    try:
        conn.execute(
            f"COPY {temp_view} TO {_sql_literal(parquet_path)} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        conn.unregister(temp_view)


def _create_view(conn: Any, view_name: str, parquet_path: Path, *, temporary: bool = False) -> None:
    view_name_sql = _quote_identifier(view_name)
    view_sql = f"{view_name_sql} AS SELECT * FROM read_parquet({_sql_literal(parquet_path)})"
    if temporary:
        conn.execute(f"CREATE OR REPLACE TEMP VIEW {view_sql}")
        return

    conn.execute(f"DROP VIEW IF EXISTS {view_name_sql}")
    conn.execute(f"CREATE VIEW {view_sql}")


def _create_runtime_views(conn: Any, config: WarehouseConfig) -> None:
    for table_name in WAREHOUSE_TABLES:
        parquet_path = config.parquet_dir / f"{table_name}.parquet"
        if parquet_path.exists():
            _create_view(conn, table_name, parquet_path, temporary=True)


def _normalize_tables(tables: Sequence[str] | None) -> tuple[str, ...]:
    if not tables:
        return tuple(WAREHOUSE_TABLES.keys())

    unknown = sorted(set(tables) - set(WAREHOUSE_TABLES))
    if unknown:
        allowed = ", ".join(WAREHOUSE_TABLES)
        raise ValueError(f"Unknown warehouse table(s): {', '.join(unknown)}. Allowed: {allowed}")
    return tuple(tables)


def _resolve_project_path(value: str | Path | None) -> Path:
    if value is None:
        return DEFAULT_ROOT_DIR
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def _sql_literal(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/").replace("'", "''")
    return f"'{value}'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _import_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is required for the analytics warehouse. Install dependencies with: "
            "pip install -r requirements.txt"
        ) from exc
    return duckdb
