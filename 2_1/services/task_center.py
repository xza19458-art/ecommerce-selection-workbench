"""Task center queries for crawl/import job logs."""

from __future__ import annotations

from typing import Any

from database.mysql_client import MySQLClient


def fetch_task_jobs(
    limit: int = 100,
    *,
    status: str | None = None,
    client: MySQLClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent crawl/import jobs from MySQL."""
    db = client or MySQLClient()
    where_sql = ""
    params: list[Any] = []

    if status and status != "全部":
        where_sql = "WHERE status = %s"
        params.append(status)

    params.append(_normalize_limit(limit))
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                  id,
                  keyword,
                  url,
                  pages,
                  status,
                  started_at,
                  finished_at,
                  total_found,
                  total_valid,
                  total_inserted,
                  error_message
                FROM crawl_jobs
                {where_sql}
                ORDER BY started_at DESC, id DESC
                LIMIT %s
                """,
                params,
            )
            rows = cursor.fetchall()
    return [_normalize_row(row) for row in rows]


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for key in ("started_at", "finished_at"):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = str(value)
    for key in ("id", "pages", "total_found", "total_valid", "total_inserted"):
        value = normalized.get(key)
        if value is not None:
            normalized[key] = int(value)
    return normalized


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 500))
