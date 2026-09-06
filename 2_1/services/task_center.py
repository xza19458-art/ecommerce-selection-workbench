"""Task center queries for crawl/import job logs."""

from __future__ import annotations

from typing import Any

from database.mysql_client import MySQLClient


IMPORT_JOB_URL_PREFIX = "local_html_import:"
TASK_TYPE_CRAWL = "爬取"
TASK_TYPE_IMPORT = "入库"


def fetch_task_jobs(
    limit: int = 100,
    *,
    status: str | None = None,
    client: MySQLClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent crawl/import jobs from MySQL."""
    db = client or MySQLClient()
    where_sql, params = _build_filters(status=status)
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


def fetch_task_jobs_page(
    limit: int = 25,
    *,
    offset: int = 0,
    keyword: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    """Fetch a filtered, paged task-center result without truncating the total."""

    db = client or MySQLClient()
    limit_value = _normalize_limit(limit)
    offset_value = _normalize_offset(offset)
    where_sql, params = _build_filters(
        keyword=keyword,
        status=status,
        job_type=job_type,
    )
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM crawl_jobs {where_sql}",
                params,
            )
            total = int((cursor.fetchone() or {}).get("total") or 0)
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
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = [_normalize_row(row) for row in cursor.fetchall()]
    return {
        "rows": rows,
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
    }


def _build_filters(
    *,
    keyword: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    keyword_value = str(keyword or "").strip()
    status_value = str(status or "").strip()
    type_value = str(job_type or "").strip().lower()

    if keyword_value:
        clauses.append("COALESCE(keyword, '') LIKE %s")
        params.append(f"%{keyword_value}%")
    if status_value and status_value not in {"all", "全部"}:
        clauses.append("status = %s")
        params.append(status_value)
    import_condition = (
        "(COALESCE(url, '') LIKE %s OR pages IS NULL "
        "OR COALESCE(total_inserted, 0) > 0)"
    )
    if type_value in {"import", "入库"}:
        clauses.append(import_condition)
        params.append(f"{IMPORT_JOB_URL_PREFIX}%")
    elif type_value in {"crawl", "爬取"}:
        clauses.append(f"NOT {import_condition}")
        params.append(f"{IMPORT_JOB_URL_PREFIX}%")

    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


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
    job_type = _infer_job_type(normalized)
    normalized["job_type"] = job_type
    normalized["type"] = job_type
    normalized["created_at"] = normalized.get("started_at")
    normalized["valid_count"] = normalized.get("total_valid") if job_type == TASK_TYPE_IMPORT else None
    normalized["ingested_count"] = normalized.get("total_inserted") if job_type == TASK_TYPE_IMPORT else None
    normalized["error"] = normalized.get("error_message")
    normalized["failure_reason"] = normalized.get("error_message")
    return normalized


def _infer_job_type(row: dict[str, Any]) -> str:
    url = str(row.get("url") or "")
    pages = row.get("pages")
    total_inserted = row.get("total_inserted") or 0
    try:
        inserted = int(total_inserted)
    except (TypeError, ValueError):
        inserted = 0
    if url.startswith(IMPORT_JOB_URL_PREFIX) or pages is None or inserted > 0:
        return TASK_TYPE_IMPORT
    return TASK_TYPE_CRAWL


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = 100
    return max(1, min(value, 500))


def _normalize_offset(offset: int) -> int:
    try:
        value = int(offset)
    except (TypeError, ValueError):
        value = 0
    return max(0, value)
