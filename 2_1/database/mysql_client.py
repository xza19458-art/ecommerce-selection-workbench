"""pymysql based persistence for Amazon product analysis."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any, Iterator

from pkg_paths import resource_path, user_data_path


CONFIG_PATH = user_data_path("config", "database.json")
SCHEMA_PATH = resource_path("database", "schema.sql")
METRIC_CENTER_MIGRATION_PATH = resource_path(
    "database", "migrations", "20260712_metric_center_v1.sql"
)
RESEARCH_WORKSPACE_MIGRATION_PATH = resource_path(
    "database", "migrations", "20260713_research_workspace_v1.sql"
)
MARKET_NICHE_MIGRATION_PATH = resource_path(
    "database", "migrations", "20260713_market_niche_v1.sql"
)


PRODUCT_TRANSLATION_COLUMNS = {
    "title_zh": "title_zh TEXT NULL COMMENT 'Chinese product title translation'",
    "title_lang": "title_lang VARCHAR(16) NULL COMMENT 'Detected product title source language'",
    "title_translation_status": "title_translation_status VARCHAR(32) NULL COMMENT 'Product title translation status'",
    "title_translation_engine": "title_translation_engine VARCHAR(64) NULL COMMENT 'Product title translation engine'",
    "title_translated_at": "title_translated_at DATETIME NULL COMMENT 'Product title translation time'",
}

PRODUCT_ATTRIBUTE_COLUMNS = {
    "product_size": "product_size VARCHAR(255) NULL COMMENT '尺寸/规格（搜索结果可见规格或标题尺寸，最佳努力采集）' AFTER category_path",
}

PRODUCT_DETAIL_COLUMNS = {
    "date_first_available": "date_first_available DATE NULL COMMENT 'Amazon Date First Available（最佳努力采集）' AFTER product_size",
    "detail_collected_at": "detail_collected_at DATETIME NULL COMMENT '最近一次有效详情页采集时间' AFTER date_first_available",
    "detail_source_file": "detail_source_file VARCHAR(1024) NULL COMMENT '最近一次详情页HTML来源文件' AFTER detail_collected_at",
}

PRODUCT_BSR_SNAPSHOTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS product_bsr_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'BSR snapshot ID',
  product_id BIGINT UNSIGNED NOT NULL COMMENT 'Product ID',
  snapshot_at DATETIME NOT NULL COMMENT 'Collection time',
  rank_value INT UNSIGNED NOT NULL COMMENT 'Best Sellers Rank',
  category_name VARCHAR(512) NOT NULL COMMENT 'Rank category name',
  category_url TEXT NULL COMMENT 'Amazon Best Sellers category URL',
  is_primary TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Primary broad-category rank',
  raw_text TEXT NULL COMMENT 'Original Best Sellers Rank text',
  source_file VARCHAR(1024) NULL COMMENT 'Saved detail HTML source',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
  PRIMARY KEY (id),
  UNIQUE KEY uk_product_bsr_snapshot_category (product_id, snapshot_at, category_name),
  KEY idx_product_bsr_time (product_id, snapshot_at),
  KEY idx_bsr_category_rank (category_name, rank_value),
  CONSTRAINT fk_product_bsr_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Amazon Best Sellers Rank time series'
"""

REVIEW_TRANSLATION_COLUMNS = {
    "title_zh": "title_zh TEXT NULL COMMENT 'Chinese review title translation'",
    "body_zh": "body_zh TEXT NULL COMMENT 'Chinese review body translation'",
    "review_lang": "review_lang VARCHAR(16) NULL COMMENT 'Detected review source language'",
    "review_translation_status": "review_translation_status VARCHAR(32) NULL COMMENT 'Review translation status'",
    "review_translation_engine": "review_translation_engine VARCHAR(64) NULL COMMENT 'Review translation engine'",
    "review_translated_at": "review_translated_at DATETIME NULL COMMENT 'Review translation time'",
}

TRANSLATION_CACHE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS translation_cache (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Translation cache ID',
  source_hash CHAR(64) NOT NULL COMMENT 'SHA256 hash of source text',
  source_lang VARCHAR(16) NOT NULL COMMENT 'Source language',
  target_lang VARCHAR(16) NOT NULL COMMENT 'Target language',
  engine VARCHAR(64) NOT NULL COMMENT 'Translation engine',
  source_text MEDIUMTEXT NOT NULL COMMENT 'Original source text',
  translated_text MEDIUMTEXT NULL COMMENT 'Translated text',
  status VARCHAR(32) NOT NULL COMMENT 'Translation status',
  error_message TEXT NULL COMMENT 'Translation error message',
  translated_at DATETIME NULL COMMENT 'Translation time',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
  PRIMARY KEY (id),
  UNIQUE KEY uk_translation_cache (source_hash, source_lang, target_lang, engine),
  KEY idx_translation_status (status),
  KEY idx_translation_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Translation result cache'
"""

KEYWORD_TRACKING_TASKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS keyword_tracking_tasks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Keyword tracking task ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT 'Marketplace',
  keyword VARCHAR(255) NOT NULL COMMENT 'Tracked keyword',
  target_snapshots INT UNSIGNED NOT NULL DEFAULT 3 COMMENT 'Target distinct snapshot_at count',
  status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT 'active/completed/paused/error',
  pages_per_keyword INT UNSIGNED NOT NULL DEFAULT 2 COMMENT 'Pages to collect per round',
  last_collected_at DATETIME NULL COMMENT 'Last successful collection/import time',
  last_checked_at DATETIME NULL COMMENT 'Last progress check time',
  achieved_snapshots INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Cached achieved distinct snapshot count',
  error_message TEXT NULL COMMENT 'Last error message',
  active_keyword VARCHAR(255)
    GENERATED ALWAYS AS (CASE WHEN status = 'active' THEN keyword ELSE NULL END) STORED
    COMMENT 'Generated key for active-task uniqueness',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
  PRIMARY KEY (id),
  UNIQUE KEY uk_keyword_tracking_active (marketplace, active_keyword),
  KEY idx_keyword_tracking_keyword (marketplace, keyword),
  KEY idx_keyword_tracking_status (status, updated_at),
  KEY idx_keyword_tracking_due (status, last_collected_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Keyword long-term tracking tasks'
"""

KEYWORD_IDEA_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS keyword_idea_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Keyword idea run ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT 'Marketplace',
  seed_keywords_json JSON NOT NULL COMMENT 'Seed keywords used for this run',
  sources_json JSON NOT NULL COMMENT 'Enabled idea sources',
  expansion_mode VARCHAR(32) NOT NULL DEFAULT 'suggest_alpha_num' COMMENT 'Expansion mode',
  status VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT 'running/completed/error',
  total_found INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Raw candidate count',
  total_saved INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Saved or merged idea count',
  warning_message TEXT NULL COMMENT 'Non-blocking warning details',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
  finished_at DATETIME NULL COMMENT 'Finished time',
  PRIMARY KEY (id),
  KEY idx_keyword_idea_runs_created (created_at),
  KEY idx_keyword_idea_runs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Keyword workshop generation runs'
"""

KEYWORD_IDEAS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS keyword_ideas (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT 'Keyword idea ID',
  marketplace VARCHAR(16) NOT NULL DEFAULT 'US' COMMENT 'Marketplace',
  keyword VARCHAR(255) NOT NULL COMMENT 'Display keyword',
  normalized_keyword VARCHAR(255) NOT NULL COMMENT 'Normalized keyword for de-duplication',
  status VARCHAR(32) NOT NULL DEFAULT 'candidate' COMMENT 'candidate/promoted/tracking/ignored',
  source_types VARCHAR(255) NOT NULL COMMENT 'Comma-separated source types',
  seed_keywords_json JSON NOT NULL COMMENT 'Seed keywords that discovered this idea',
  evidence_json JSON NULL COMMENT 'Source evidence and scoring signals',
  idea_score DECIMAL(6,2) NOT NULL DEFAULT 0 COMMENT 'Early idea score',
  confidence_score DECIMAL(6,2) NOT NULL DEFAULT 0 COMMENT 'Evidence confidence score',
  recommendation_level VARCHAR(32) NOT NULL DEFAULT '仅作灵感' COMMENT 'Chinese recommendation label',
  reason TEXT NOT NULL COMMENT 'Chinese scoring reason',
  occurrence_count INT UNSIGNED NOT NULL DEFAULT 1 COMMENT 'Current evidence strength count',
  last_run_id BIGINT UNSIGNED NULL COMMENT 'Latest keyword idea run ID',
  promoted_keyword_id BIGINT UNSIGNED NULL COMMENT 'Promoted keyword ID',
  tracking_task_id BIGINT UNSIGNED NULL COMMENT 'Created tracking task ID',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'Created time',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'Updated time',
  PRIMARY KEY (id),
  UNIQUE KEY uk_keyword_idea_market_norm (marketplace, normalized_keyword),
  KEY idx_keyword_idea_status_score (status, idea_score),
  KEY idx_keyword_idea_updated (updated_at),
  KEY idx_keyword_idea_source (source_types),
  CONSTRAINT fk_keyword_ideas_last_run
    FOREIGN KEY (last_run_id) REFERENCES keyword_idea_runs(id)
    ON DELETE SET NULL,
  CONSTRAINT fk_keyword_ideas_keyword
    FOREIGN KEY (promoted_keyword_id) REFERENCES keywords(id)
    ON DELETE SET NULL,
  CONSTRAINT fk_keyword_ideas_tracking
    FOREIGN KEY (tracking_task_id) REFERENCES keyword_tracking_tasks(id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Keyword workshop candidate ideas'
"""


class DatabaseConfigError(RuntimeError):
    """Raised when MySQL configuration or dependency is missing."""


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"

    @classmethod
    def from_file(cls, path: Path = CONFIG_PATH) -> "DatabaseConfig":
        if not path.exists():
            raise DatabaseConfigError(
                f"未找到数据库配置文件: {path}. 请复制 config/database.example.json 为 database.json 后填写 MySQL 信息。"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            host=data.get("host", "127.0.0.1"),
            port=int(data.get("port", 3306)),
            user=data["user"],
            password=data.get("password", ""),
            database=data.get("database", "amazon_selection"),
            charset=data.get("charset", "utf8mb4"),
        )


def _import_pymysql():
    try:
        import pymysql
    except ImportError as exc:
        raise DatabaseConfigError("未安装 pymysql，请先执行: pip install pymysql") from exc
    return pymysql


class MySQLClient:
    def __init__(self, config: DatabaseConfig | None = None) -> None:
        self.config = config or DatabaseConfig.from_file()
        self._pymysql = _import_pymysql()

    @contextmanager
    def connect(self, database: str | None = None) -> Iterator[Any]:
        selected_database = self.config.database if database is None else (database or None)
        conn = self._pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=selected_database,
            charset=self.config.charset,
            autocommit=False,
            cursorclass=self._pymysql.cursors.DictCursor,
        )
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize_schema(self) -> dict[str, Any]:
        """Create or upgrade the configured database under the migration ledger."""
        database_sql = _quote_mysql_identifier(self.config.database)
        with self.connect(database="") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS {database_sql} "
                    "DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
                )
        from database.migration_runner import MigrationManager

        return MigrationManager(self).initialize(
            schema_path=SCHEMA_PATH,
            legacy_bootstrap=self._bootstrap_legacy_schema,
        )

    def _bootstrap_legacy_schema(self, cursor: Any) -> None:
        """Guarded one-time upgrade path for databases created before the ledger."""
        self.ensure_translation_columns(cursor)
        self.ensure_product_detail_schema(cursor)
        self.ensure_translation_cache_table(cursor)
        self.ensure_keyword_tracking_table(cursor)
        self.ensure_keyword_workshop_tables(cursor)
        self.ensure_metric_center_schema(cursor)
        self.ensure_research_workspace_schema(cursor)
        self.ensure_market_niche_schema(cursor)
        cursor.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = 'product_snapshots'
              AND column_name = 'monthly_bought'
            """,
            (self.config.database,),
        )
        row = cursor.fetchone() or {}
        if str(row.get("is_nullable") or row.get("IS_NULLABLE") or "").upper() != "YES":
            cursor.execute(
                "ALTER TABLE product_snapshots "
                "MODIFY COLUMN monthly_bought INT UNSIGNED NULL "
                "COMMENT '近月购买量（缺失徽标=NULL=未知）'"
            )

    def upsert_keyword(self, cursor: Any, keyword: str | None, marketplace: str) -> int | None:
        if not keyword:
            return None
        cursor.execute(
            """
            INSERT INTO keywords (marketplace, keyword)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
            """,
            (marketplace, keyword),
        )
        return int(cursor.lastrowid)

    def upsert_product(self, cursor: Any, record: Any) -> int:
        cursor.execute(
            """
            INSERT INTO products (
              marketplace, asin, title, title_zh, title_lang, title_translation_status,
              title_translation_engine, title_translated_at, brand, category_path, product_size, product_url, image_url,
              first_seen_at, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              id = LAST_INSERT_ID(id),
              title = VALUES(title),
              title_zh = COALESCE(VALUES(title_zh), title_zh),
              title_lang = COALESCE(VALUES(title_lang), title_lang),
              title_translation_status = CASE
                WHEN VALUES(title_zh) IS NOT NULL THEN VALUES(title_translation_status)
                WHEN title_translation_status IS NULL THEN VALUES(title_translation_status)
                ELSE title_translation_status
              END,
              title_translation_engine = CASE
                WHEN VALUES(title_zh) IS NOT NULL THEN VALUES(title_translation_engine)
                WHEN title_translation_engine IS NULL THEN VALUES(title_translation_engine)
                ELSE title_translation_engine
              END,
              title_translated_at = CASE
                WHEN VALUES(title_zh) IS NOT NULL THEN VALUES(title_translated_at)
                ELSE title_translated_at
              END,
              brand = COALESCE(VALUES(brand), brand),
              category_path = COALESCE(VALUES(category_path), category_path),
              product_size = COALESCE(NULLIF(VALUES(product_size), ''), product_size),
              product_url = VALUES(product_url),
              image_url = VALUES(image_url),
              last_seen_at = VALUES(last_seen_at)
            """,
            (
                record.marketplace,
                record.asin,
                record.title,
                getattr(record, "title_zh", None),
                getattr(record, "title_lang", None),
                getattr(record, "title_translation_status", None),
                getattr(record, "title_translation_engine", None),
                getattr(record, "title_translated_at", None),
                record.brand,
                record.category_path,
                getattr(record, "product_size", None),
                record.product_url,
                record.image_url,
                record.snapshot_at,
                record.snapshot_at,
            ),
        )
        return int(cursor.lastrowid)

    def ensure_translation_columns(self, cursor: Any) -> None:
        self._ensure_table_columns(cursor, "products", PRODUCT_TRANSLATION_COLUMNS)
        self._ensure_table_columns(cursor, "product_reviews", REVIEW_TRANSLATION_COLUMNS)

    def ensure_product_attribute_columns(self, cursor: Any) -> None:
        self._ensure_table_columns(cursor, "products", PRODUCT_ATTRIBUTE_COLUMNS)

    def ensure_product_detail_schema(self, cursor: Any) -> None:
        self.ensure_product_attribute_columns(cursor)
        self._ensure_table_columns(cursor, "products", PRODUCT_DETAIL_COLUMNS)
        cursor.execute(PRODUCT_BSR_SNAPSHOTS_TABLE_SQL)

    def ensure_metric_center_schema(self, cursor: Any) -> None:
        """Create additive V1 metric tables without changing existing scores."""
        if not METRIC_CENTER_MIGRATION_PATH.exists():
            raise DatabaseConfigError(f"未找到指标中心迁移文件: {METRIC_CENTER_MIGRATION_PATH}")
        for statement in _split_sql(METRIC_CENTER_MIGRATION_PATH.read_text(encoding="utf-8")):
            if statement.lstrip().upper().startswith("USE "):
                continue
            cursor.execute(statement)

    def ensure_research_workspace_schema(self, cursor: Any) -> None:
        """Create additive V1 research workspace tables."""
        if not RESEARCH_WORKSPACE_MIGRATION_PATH.exists():
            raise DatabaseConfigError(f"未找到研究项目迁移文件: {RESEARCH_WORKSPACE_MIGRATION_PATH}")
        for statement in _split_sql(RESEARCH_WORKSPACE_MIGRATION_PATH.read_text(encoding="utf-8")):
            if statement.lstrip().upper().startswith("USE "):
                continue
            cursor.execute(statement)

    def ensure_market_niche_schema(self, cursor: Any) -> None:
        """Create additive V1 market-niche tables and their dependencies."""
        self.ensure_research_workspace_schema(cursor)
        self.ensure_metric_center_schema(cursor)
        if not MARKET_NICHE_MIGRATION_PATH.exists():
            raise DatabaseConfigError(f"未找到市场利基迁移文件: {MARKET_NICHE_MIGRATION_PATH}")
        for statement in _split_sql(MARKET_NICHE_MIGRATION_PATH.read_text(encoding="utf-8")):
            if statement.lstrip().upper().startswith("USE "):
                continue
            cursor.execute(statement)

    def ensure_translation_cache_table(self, cursor: Any) -> None:
        cursor.execute(TRANSLATION_CACHE_TABLE_SQL)

    def ensure_keyword_tracking_table(self, cursor: Any) -> None:
        cursor.execute(KEYWORD_TRACKING_TASKS_TABLE_SQL)

    def ensure_keyword_workshop_tables(self, cursor: Any) -> None:
        cursor.execute(KEYWORD_TRACKING_TASKS_TABLE_SQL)
        cursor.execute(KEYWORD_IDEA_RUNS_TABLE_SQL)
        cursor.execute(KEYWORD_IDEAS_TABLE_SQL)

    def has_columns(self, cursor: Any, table: str, columns: list[str] | tuple[str, ...]) -> bool:
        existing = self._fetch_existing_columns(cursor, table)
        return all(column in existing for column in columns)

    def has_table(self, cursor: Any, table: str) -> bool:
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
            """,
            (self.config.database, table),
        )
        row = cursor.fetchone() or {}
        return int(row.get("total") or row.get("COUNT(*)") or 0) > 0

    def _ensure_table_columns(self, cursor: Any, table: str, columns: dict[str, str]) -> None:
        existing = self._fetch_existing_columns(cursor, table)
        for name, definition in columns.items():
            if name not in existing:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    def _fetch_existing_columns(self, cursor: Any, table: str) -> set[str]:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
            """,
            (self.config.database, table),
        )
        return {str(row.get("column_name") or row.get("COLUMN_NAME")) for row in cursor.fetchall()}

    def upsert_snapshot(self, cursor: Any, product_id: int, record: Any) -> None:
        cursor.execute(
            """
            INSERT INTO product_snapshots (
              product_id, snapshot_at, price, rating, review_count, monthly_bought,
              is_deal, is_sponsored, page_no, organic_rank, raw_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              price = VALUES(price),
              rating = VALUES(rating),
              review_count = VALUES(review_count),
              monthly_bought = VALUES(monthly_bought),
              is_deal = VALUES(is_deal),
              is_sponsored = VALUES(is_sponsored),
              page_no = VALUES(page_no),
              organic_rank = VALUES(organic_rank),
              raw_json = VALUES(raw_json)
            """,
            (
                product_id,
                record.snapshot_at,
                record.price,
                record.rating,
                record.review_count,
                record.monthly_bought,
                int(record.is_deal),
                int(record.is_sponsored),
                record.page_no,
                record.organic_rank,
                json.dumps(record.to_storage_dict(), ensure_ascii=False),
            ),
        )

    def upsert_keyword_rank(self, cursor: Any, keyword_id: int | None, product_id: int, record: Any) -> None:
        if keyword_id is None:
            return
        cursor.execute(
            """
            INSERT INTO keyword_rank_snapshots (
              keyword_id, product_id, snapshot_at, page_no, organic_rank, is_sponsored
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              page_no = VALUES(page_no),
              organic_rank = VALUES(organic_rank),
              is_sponsored = VALUES(is_sponsored)
            """,
            (
                keyword_id,
                product_id,
                record.snapshot_at,
                record.page_no,
                record.organic_rank,
                int(record.is_sponsored),
            ),
        )

    def upsert_score(self, cursor: Any, product_id: int, keyword_id: int | None, score: Any, score_date: date) -> None:
        score_values = (
            score.total_score,
            score.demand_score,
            score.growth_score,
            score.competition_score,
            score.rating_score,
            score.price_score,
            score.rank_score,
            score.reason,
        )
        if keyword_id is None:
            # MySQL unique indexes permit multiple NULL values, so the composite
            # unique key cannot deduplicate legacy scores without a keyword.
            cursor.execute(
                """
                UPDATE product_scores
                SET total_score = %s,
                    demand_score = %s,
                    growth_score = %s,
                    competition_score = %s,
                    rating_score = %s,
                    price_score = %s,
                    rank_score = %s,
                    reason = %s
                WHERE product_id = %s
                  AND keyword_id IS NULL
                  AND score_date = %s
                """,
                (*score_values, product_id, score_date),
            )
            if int(cursor.rowcount or 0) > 0:
                return
        cursor.execute(
            """
            INSERT INTO product_scores (
              product_id, keyword_id, score_date, total_score, demand_score, growth_score,
              competition_score, rating_score, price_score, rank_score, reason
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              total_score = VALUES(total_score),
              demand_score = VALUES(demand_score),
              growth_score = VALUES(growth_score),
              competition_score = VALUES(competition_score),
              rating_score = VALUES(rating_score),
              price_score = VALUES(price_score),
              rank_score = VALUES(rank_score),
              reason = VALUES(reason)
            """,
            (
                product_id,
                keyword_id,
                score_date,
                *score_values,
            ),
        )

    def create_job(self, cursor: Any, keyword: str | None, url: str | None, pages: int | None) -> int:
        cursor.execute(
            """
            INSERT INTO crawl_jobs (keyword, url, pages, status, started_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (keyword, url, pages, "运行中", datetime.now()),
        )
        return int(cursor.lastrowid)

    def finish_job(
        self,
        cursor: Any,
        job_id: int,
        status: str,
        total_found: int,
        total_valid: int,
        total_inserted: int,
        error_message: str | None = None,
    ) -> None:
        cursor.execute(
            """
            UPDATE crawl_jobs
            SET status = %s,
                finished_at = %s,
                total_found = %s,
                total_valid = %s,
                total_inserted = %s,
                error_message = %s
            WHERE id = %s
            """,
            (status, datetime.now(), total_found, total_valid, total_inserted, error_message, job_id),
        )


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).strip().rstrip(";")
            if statement:
                statements.append(statement)
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return statements


def _quote_mysql_identifier(value: str) -> str:
    identifier = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_$]{1,64}", identifier):
        raise DatabaseConfigError("数据库名称只能包含字母、数字、下划线或 $，且长度不能超过 64。")
    return f"`{identifier}`"
