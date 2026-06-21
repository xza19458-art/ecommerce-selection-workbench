"""pymysql based persistence for Amazon product analysis."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Iterator


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "database.json"


PRODUCT_TRANSLATION_COLUMNS = {
    "title_zh": "title_zh TEXT NULL COMMENT 'Chinese product title translation'",
    "title_lang": "title_lang VARCHAR(16) NULL COMMENT 'Detected product title source language'",
    "title_translation_status": "title_translation_status VARCHAR(32) NULL COMMENT 'Product title translation status'",
    "title_translation_engine": "title_translation_engine VARCHAR(64) NULL COMMENT 'Product title translation engine'",
    "title_translated_at": "title_translated_at DATETIME NULL COMMENT 'Product title translation time'",
}

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

    def initialize_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        statements = _split_sql(schema_path.read_text(encoding="utf-8"))
        with self.connect(database="") as conn:
            with conn.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)

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
              title_translation_engine, title_translated_at, brand, category_path, product_url, image_url,
              first_seen_at, last_seen_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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

    def ensure_translation_cache_table(self, cursor: Any) -> None:
        cursor.execute(TRANSLATION_CACHE_TABLE_SQL)

    def ensure_keyword_tracking_table(self, cursor: Any) -> None:
        cursor.execute(KEYWORD_TRACKING_TASKS_TABLE_SQL)

    def has_columns(self, cursor: Any, table: str, columns: list[str] | tuple[str, ...]) -> bool:
        existing = self._fetch_existing_columns(cursor, table)
        return all(column in existing for column in columns)

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
                score.total_score,
                score.demand_score,
                score.growth_score,
                score.competition_score,
                score.rating_score,
                score.price_score,
                score.rank_score,
                score.reason,
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
