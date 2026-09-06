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
from pkg_paths import user_data_path


ROOT = user_data_path()
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
          p.product_size,
          p.date_first_available,
          p.detail_collected_at,
          p.detail_source_file,
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
    "fact_product_bsr_snapshots": """
        SELECT
          b.id AS bsr_snapshot_id,
          b.product_id,
          p.marketplace,
          p.asin,
          b.snapshot_at,
          b.rank_value,
          b.category_name,
          b.category_url,
          b.is_primary,
          b.raw_text,
          b.source_file,
          b.created_at
        FROM product_bsr_snapshots b
        JOIN products p ON p.id = b.product_id
    """,
    "dim_product_physical_specs": """
        SELECT
          s.id AS spec_id,
          s.product_id,
          p.marketplace,
          p.asin,
          s.parent_asin,
          s.item_length_in,
          s.item_width_in,
          s.item_height_in,
          s.package_length_in,
          s.package_width_in,
          s.package_height_in,
          s.item_weight_oz,
          s.package_weight_oz,
          s.unit_count,
          s.model_number,
          s.raw_dimensions_json,
          s.raw_weight_json,
          s.source_file,
          s.collected_at,
          s.created_at,
          s.updated_at
        FROM product_physical_specs s
        JOIN products p ON p.id = s.product_id
    """,
    "fact_product_offer_snapshots": """
        SELECT
          o.id AS offer_snapshot_id,
          o.product_id,
          p.marketplace,
          p.asin,
          o.snapshot_at,
          o.current_price,
          o.list_price,
          o.currency,
          o.discount_percent,
          o.coupon_text,
          o.availability_status,
          o.featured_offer_seller,
          o.ships_from,
          o.fulfillment_channel,
          o.is_prime,
          o.offer_count,
          o.badges_json,
          o.image_count,
          o.video_count,
          o.bullet_count,
          o.has_a_plus,
          o.rating_histogram_json,
          o.postal_code,
          o.source_file,
          o.raw_json,
          o.created_at
        FROM product_offer_snapshots o
        JOIN products p ON p.id = o.product_id
    """,
    "bridge_product_variants": """
        SELECT
          v.id AS variant_relation_id,
          v.source_product_id,
          v.marketplace,
          v.parent_asin,
          v.child_asin,
          v.attributes_json,
          v.product_url,
          v.is_selected,
          v.first_seen_at,
          v.last_seen_at,
          v.source_file,
          v.created_at,
          v.updated_at
        FROM product_variants v
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
    "fact_keyword_serp_snapshots": """
        SELECT
          s.id AS serp_snapshot_id,
          s.keyword_id,
          k.marketplace,
          k.keyword,
          s.snapshot_at,
          s.page_count,
          s.total_card_count,
          s.organic_count,
          s.sponsored_count,
          s.unique_asin_count,
          s.ad_density,
          s.price_p25,
          s.price_median,
          s.price_p75,
          s.review_p25,
          s.review_median,
          s.review_p75,
          s.rating_median,
          s.monthly_bought_median,
          s.demand_cr3,
          s.demand_cr10,
          s.data_coverage,
          s.raw_json,
          s.created_at
        FROM keyword_serp_snapshots s
        JOIN keywords k ON k.id = s.keyword_id
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
    "fact_product_metric_inputs": """
        SELECT
          i.id AS metric_input_id,
          i.product_id,
          p.marketplace,
          p.asin,
          i.period_start,
          i.period_end,
          i.source_type,
          i.source_label,
          i.sessions,
          i.page_views,
          i.units_ordered,
          i.orders,
          i.ordered_sales,
          i.featured_offer_percentage,
          i.impressions,
          i.clicks,
          i.cart_adds,
          i.purchases,
          i.ad_spend,
          i.ad_clicks,
          i.ad_orders,
          i.ad_sales,
          i.total_sales,
          i.unit_purchase_cost,
          i.unit_shipping_cost,
          i.unit_fba_fee,
          i.unit_referral_fee,
          i.unit_other_cost,
          i.assumed_cvr_low,
          i.assumed_cvr_base,
          i.assumed_cvr_high,
          i.notes,
          i.raw_json,
          i.created_at,
          i.updated_at
        FROM product_metric_inputs i
        JOIN products p ON p.id = i.product_id
    """,
    "fact_product_estimates": """
        SELECT
          e.id AS estimate_id,
          e.product_id,
          p.marketplace,
          p.asin,
          e.as_of_date,
          e.metric_key,
          e.value_low,
          e.value_base,
          e.value_high,
          e.unit,
          e.model_version,
          e.confidence_score,
          e.confidence_level,
          e.method,
          e.evidence_json,
          e.created_at,
          e.updated_at
        FROM product_estimates e
        JOIN products p ON p.id = e.product_id
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

WAREHOUSE_SOURCE_TABLES = {
    "dim_products": "products",
    "dim_keywords": "keywords",
    "fact_product_snapshots": "product_snapshots",
    "fact_product_bsr_snapshots": "product_bsr_snapshots",
    "dim_product_physical_specs": "product_physical_specs",
    "fact_product_offer_snapshots": "product_offer_snapshots",
    "bridge_product_variants": "product_variants",
    "fact_keyword_rank_snapshots": "keyword_rank_snapshots",
    "fact_keyword_serp_snapshots": "keyword_serp_snapshots",
    "fact_product_scores": "product_scores",
    "fact_product_metric_inputs": "product_metric_inputs",
    "fact_product_estimates": "product_estimates",
    "fact_product_reviews": "product_reviews",
    "mart_review_insights": "product_review_insights",
}

_MARKER_COLUMN_PRIORITY = (
    "updated_at",
    "collected_at",
    "snapshot_at",
    "score_date",
    "insight_date",
    "last_seen_at",
    "created_at",
)
MANIFEST_FILE_NAME = "warehouse_manifest.json"


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
    synced_at: datetime
    manifest_path: Path

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
    source_markers: dict[str, dict[str, Any]] = {}
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
                source_markers = {
                    table_name: _source_table_marker(cursor, WAREHOUSE_SOURCE_TABLES[table_name])
                    for table_name in selected_tables
                }

    synced_at = datetime.now().replace(microsecond=0)
    manifest_path = _write_warehouse_manifest(
        warehouse_config,
        selected_tables,
        summaries,
        source_markers,
        synced_at=synced_at,
    )

    return WarehouseSyncSummary(
        duckdb_path=warehouse_config.duckdb_path,
        parquet_dir=warehouse_config.parquet_dir,
        tables=tuple(summaries),
        synced_at=synced_at,
        manifest_path=manifest_path,
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


def get_warehouse_status(
    *,
    config: WarehouseConfig | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    warehouse_config = config or WarehouseConfig.from_file()
    db = client or MySQLClient()
    manifest_path = warehouse_config.root_dir / MANIFEST_FILE_NAME
    manifest = _read_warehouse_manifest(manifest_path)
    manifest_tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}

    current_markers: dict[str, dict[str, Any]] = {}
    with db.connect() as conn:
        with conn.cursor() as cursor:
            for warehouse_table, source_table in WAREHOUSE_SOURCE_TABLES.items():
                current_markers[warehouse_table] = _source_table_marker(cursor, source_table)

    rows: list[dict[str, Any]] = []
    stale_tables: list[str] = []
    missing_tables: list[str] = []
    for table_name in WAREHOUSE_TABLES:
        parquet_path = warehouse_config.parquet_dir / f"{table_name}.parquet"
        recorded = manifest_tables.get(table_name) if isinstance(manifest_tables, dict) else None
        current = current_markers[table_name]
        exists = parquet_path.is_file()
        marker_matches = bool(recorded) and recorded.get("source") == current
        if not exists or not recorded:
            state = "missing"
            missing_tables.append(table_name)
        elif not marker_matches:
            state = "stale"
            stale_tables.append(table_name)
        else:
            state = "current"
        rows.append(
            {
                "name": table_name,
                "source_table": WAREHOUSE_SOURCE_TABLES[table_name],
                "state": state,
                "rows": int((recorded or {}).get("rows") or 0),
                "source_rows": int(current.get("rows") or 0),
                "parquet_path": str(parquet_path),
            }
        )

    if missing_tables:
        status = "missing"
        message = f"分析仓库缺少 {len(missing_tables)} 张表，请先执行同步。"
    elif stale_tables:
        status = "stale"
        message = f"MySQL 已变化，分析仓库有 {len(stale_tables)} 张表需要同步。"
    else:
        status = "current"
        message = "分析仓库与当前 MySQL 来源标记一致。"
    return {
        "status": status,
        "message": message,
        "last_synced_at": manifest.get("synced_at"),
        "manifest_path": str(manifest_path),
        "duckdb_path": str(warehouse_config.duckdb_path),
        "parquet_dir": str(warehouse_config.parquet_dir),
        "stale_tables": stale_tables,
        "missing_tables": missing_tables,
        "tables": rows,
    }


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
    temp_path = parquet_path.with_suffix(parquet_path.suffix + ".tmp")
    temp_path.unlink(missing_ok=True)
    conn.register(temp_view, dataframe)
    try:
        conn.execute(
            f"COPY {temp_view} TO {_sql_literal(temp_path)} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        temp_path.replace(parquet_path)
    finally:
        conn.unregister(temp_view)
        temp_path.unlink(missing_ok=True)


def _source_table_marker(cursor: Any, table_name: str) -> dict[str, Any]:
    table_sql = _quote_mysql_identifier(table_name)
    cursor.execute(f"SHOW COLUMNS FROM {table_sql}")
    columns = {
        str(row.get("Field") or row.get("field") or row.get("COLUMN_NAME") or "")
        for row in cursor.fetchall()
    }
    marker_column = next((column for column in _MARKER_COLUMN_PRIORITY if column in columns), None)
    marker_select = (
        f", MAX({_quote_mysql_identifier(marker_column)}) AS max_marker"
        if marker_column
        else ", NULL AS max_marker"
    )
    max_id_select = ", MAX(`id`) AS max_id" if "id" in columns else ", NULL AS max_id"
    cursor.execute(f"SELECT COUNT(*) AS row_count{max_id_select}{marker_select} FROM {table_sql}")
    row = cursor.fetchone() or {}
    return {
        "rows": int(row.get("row_count") or 0),
        "max_id": int(row.get("max_id") or 0) if row.get("max_id") is not None else None,
        "marker_column": marker_column,
        "max_marker": str(row.get("max_marker")) if row.get("max_marker") is not None else None,
    }


def _write_warehouse_manifest(
    config: WarehouseConfig,
    selected_tables: Sequence[str],
    summaries: Sequence[WarehouseTableSummary],
    source_markers: dict[str, dict[str, Any]],
    *,
    synced_at: datetime,
) -> Path:
    path = config.root_dir / MANIFEST_FILE_NAME
    existing = _read_warehouse_manifest(path)
    table_state = existing.get("tables") if isinstance(existing.get("tables"), dict) else {}
    table_state = dict(table_state)
    summary_by_name = {summary.name: summary for summary in summaries}
    for table_name in selected_tables:
        summary = summary_by_name[table_name]
        table_state[table_name] = {
            "rows": summary.rows,
            "parquet_path": str(summary.parquet_path),
            "synced_at": synced_at.isoformat(sep=" "),
            "source": source_markers[table_name],
        }
    payload = {
        "schema_version": 1,
        "synced_at": synced_at.isoformat(sep=" "),
        "duckdb_path": str(config.duckdb_path),
        "parquet_dir": str(config.parquet_dir),
        "tables": table_state,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    return path


def _read_warehouse_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def _quote_mysql_identifier(value: str) -> str:
    identifier = str(value or "")
    if not identifier or not identifier.replace("_", "").isalnum():
        raise ValueError(f"Invalid MySQL identifier: {value}")
    return f"`{identifier}`"


def _import_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is required for the analytics warehouse. Install dependencies with: "
            "pip install -r requirements.txt"
        ) from exc
    return duckdb
