from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.mysql_client import MySQLClient


PRODUCT_COLUMNS = (
    "title_zh",
    "title_lang",
    "title_translation_status",
    "title_translation_engine",
    "title_translated_at",
)

REVIEW_COLUMNS = (
    "title_zh",
    "body_zh",
    "review_lang",
    "review_translation_status",
    "review_translation_engine",
    "review_translated_at",
)


def main() -> None:
    db = MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            before_products = db.has_columns(cursor, "products", PRODUCT_COLUMNS)
            before_reviews = db.has_columns(cursor, "product_reviews", REVIEW_COLUMNS)
            before_cache = _table_exists(cursor, db.config.database, "translation_cache")

            db.ensure_translation_columns(cursor)
            db.ensure_translation_cache_table(cursor)

            after_products = db.has_columns(cursor, "products", PRODUCT_COLUMNS)
            after_reviews = db.has_columns(cursor, "product_reviews", REVIEW_COLUMNS)
            after_cache = _table_exists(cursor, db.config.database, "translation_cache")

    print("Translation schema migration")
    print("=" * 32)
    print(f"products_columns_before: {before_products}")
    print(f"reviews_columns_before: {before_reviews}")
    print(f"cache_table_before: {before_cache}")
    print(f"products_columns_after: {after_products}")
    print(f"reviews_columns_after: {after_reviews}")
    print(f"cache_table_after: {after_cache}")


def _table_exists(cursor, database: str, table: str) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name = %s
        """,
        (database, table),
    )
    return int(cursor.fetchone()["count"]) > 0


if __name__ == "__main__":
    main()
