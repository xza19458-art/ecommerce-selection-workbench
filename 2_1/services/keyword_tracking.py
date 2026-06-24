"""CRUD and progress helpers for keyword tracking tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from database.mysql_client import MySQLClient
from services.settings import CollectionLimits, get_collection_limits


STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_PAUSED = "paused"
STATUS_ERROR = "error"
VALID_STATUSES = {STATUS_ACTIVE, STATUS_COMPLETED, STATUS_PAUSED, STATUS_ERROR}


@dataclass(frozen=True)
class KeywordTrackingTask:
    id: int
    marketplace: str
    keyword: str
    target_snapshots: int
    status: str
    pages_per_keyword: int
    last_collected_at: str | None
    last_checked_at: str | None
    achieved_snapshots: int
    current_snapshots: int
    error_message: str | None
    created_at: str | None
    updated_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "marketplace": self.marketplace,
            "keyword": self.keyword,
            "target_snapshots": self.target_snapshots,
            "status": self.status,
            "pages_per_keyword": self.pages_per_keyword,
            "last_collected_at": self.last_collected_at,
            "last_checked_at": self.last_checked_at,
            "achieved_snapshots": self.achieved_snapshots,
            "current_snapshots": self.current_snapshots,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def ensure_keyword_tracking_schema(*, client: MySQLClient | None = None) -> None:
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_tracking_table(cursor)


def create_tracking_task(
    *,
    marketplace: str = "US",
    keyword: str,
    target_snapshots: int = 3,
    pages_per_keyword: int | None = None,
    client: MySQLClient | None = None,
) -> KeywordTrackingTask:
    db = client or MySQLClient()
    marketplace = _normalize_marketplace(marketplace)
    keyword = _normalize_keyword(keyword)
    target_snapshots = _normalize_positive_int(target_snapshots, default=3, maximum=365)
    pages_per_keyword = normalize_tracking_pages_per_keyword(pages_per_keyword)

    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_tracking_table(cursor)
            achieved = _count_keyword_snapshot_times(cursor, marketplace, keyword)
            status = STATUS_COMPLETED if achieved >= target_snapshots else STATUS_ACTIVE
            cursor.execute(
                """
                INSERT INTO keyword_tracking_tasks (
                  marketplace, keyword, target_snapshots, status, pages_per_keyword,
                  achieved_snapshots, last_checked_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (marketplace, keyword, target_snapshots, status, pages_per_keyword, achieved, datetime.now()),
            )
            task_id = int(cursor.lastrowid)
            return _fetch_task_by_id(cursor, task_id)


def list_tracking_tasks(
    *,
    status: str | None = None,
    marketplace: str | None = None,
    keyword: str | None = None,
    limit: int = 200,
    client: MySQLClient | None = None,
) -> list[KeywordTrackingTask]:
    db = client or MySQLClient()
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        _validate_status(status)
        clauses.append("t.status = %s")
        params.append(status)
    if marketplace:
        clauses.append("t.marketplace = %s")
        params.append(_normalize_marketplace(marketplace))
    if keyword:
        clauses.append("t.keyword LIKE %s")
        params.append(f"%{keyword.strip()}%")
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(_normalize_limit(limit))

    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_tracking_table(cursor)
            cursor.execute(
                f"""
                SELECT
                  t.*,
                  COALESCE(kstats.current_snapshots, 0) AS current_snapshots
                FROM keyword_tracking_tasks t
                LEFT JOIN (
                  SELECT
                    k.marketplace,
                    k.keyword,
                    COUNT(DISTINCT krs.snapshot_at) AS current_snapshots
                  FROM keywords k
                  JOIN keyword_rank_snapshots krs ON krs.keyword_id = k.id
                  GROUP BY k.marketplace, k.keyword
                ) kstats
                  ON kstats.marketplace = t.marketplace
                 AND kstats.keyword = t.keyword
                {where_sql}
                ORDER BY
                  CASE t.status
                    WHEN 'active' THEN 1
                    WHEN 'error' THEN 2
                    WHEN 'paused' THEN 3
                    WHEN 'completed' THEN 4
                    ELSE 5
                  END,
                  t.updated_at DESC
                LIMIT %s
                """,
                params,
            )
            return [_row_to_task(row) for row in cursor.fetchall()]


def get_tracking_task(task_id: int, *, client: MySQLClient | None = None) -> KeywordTrackingTask | None:
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_tracking_table(cursor)
            return _fetch_task_by_id(cursor, int(task_id), missing_ok=True)


def count_keyword_snapshot_times(
    *,
    marketplace: str = "US",
    keyword: str,
    client: MySQLClient | None = None,
) -> int:
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            return _count_keyword_snapshot_times(cursor, _normalize_marketplace(marketplace), _normalize_keyword(keyword))


def refresh_tracking_task_progress(
    task_id: int,
    *,
    client: MySQLClient | None = None,
) -> KeywordTrackingTask:
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_tracking_table(cursor)
            task = _fetch_task_by_id(cursor, int(task_id))
            achieved = _count_keyword_snapshot_times(cursor, task.marketplace, task.keyword)
            next_status = STATUS_COMPLETED if task.status == STATUS_ACTIVE and achieved >= task.target_snapshots else task.status
            cursor.execute(
                """
                UPDATE keyword_tracking_tasks
                SET achieved_snapshots = %s,
                    status = %s,
                    last_checked_at = %s,
                    error_message = CASE WHEN %s = 'error' THEN error_message ELSE NULL END
                WHERE id = %s
                """,
                (achieved, next_status, datetime.now(), next_status, task.id),
            )
            return _fetch_task_by_id(cursor, task.id)


def refresh_all_tracking_task_progress(
    *,
    status: str | None = STATUS_ACTIVE,
    client: MySQLClient | None = None,
) -> list[KeywordTrackingTask]:
    tasks = list_tracking_tasks(status=status, client=client)
    return [refresh_tracking_task_progress(task.id, client=client) for task in tasks]


def update_tracking_task_status(
    task_id: int,
    status: str,
    *,
    error_message: str | None = None,
    client: MySQLClient | None = None,
) -> KeywordTrackingTask:
    _validate_status(status)
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_tracking_table(cursor)
            cursor.execute(
                """
                UPDATE keyword_tracking_tasks
                SET status = %s,
                    error_message = %s,
                    last_checked_at = %s
                WHERE id = %s
                """,
                (status, error_message if status == STATUS_ERROR else None, datetime.now(), int(task_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"关键词追踪任务不存在: {task_id}")
            return _fetch_task_by_id(cursor, int(task_id))


def record_tracking_collection(
    task_id: int,
    *,
    collected_at: datetime | None = None,
    error_message: str | None = None,
    client: MySQLClient | None = None,
) -> KeywordTrackingTask:
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_tracking_table(cursor)
            task = _fetch_task_by_id(cursor, int(task_id))
            if error_message:
                cursor.execute(
                    """
                    UPDATE keyword_tracking_tasks
                    SET status = %s,
                        error_message = %s,
                        last_checked_at = %s
                    WHERE id = %s
                    """,
                    (STATUS_ERROR, error_message, datetime.now(), task.id),
                )
                return _fetch_task_by_id(cursor, task.id)

            achieved = _count_keyword_snapshot_times(cursor, task.marketplace, task.keyword)
            next_status = STATUS_COMPLETED if achieved >= task.target_snapshots else STATUS_ACTIVE
            cursor.execute(
                """
                UPDATE keyword_tracking_tasks
                SET last_collected_at = %s,
                    last_checked_at = %s,
                    achieved_snapshots = %s,
                    status = %s,
                    error_message = NULL
                WHERE id = %s
                """,
                (collected_at or datetime.now(), datetime.now(), achieved, next_status, task.id),
            )
            return _fetch_task_by_id(cursor, task.id)


def delete_tracking_task(task_id: int, *, client: MySQLClient | None = None) -> bool:
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.ensure_keyword_tracking_table(cursor)
            cursor.execute("DELETE FROM keyword_tracking_tasks WHERE id = %s", (int(task_id),))
            return cursor.rowcount > 0


def _fetch_task_by_id(cursor: Any, task_id: int, *, missing_ok: bool = False) -> KeywordTrackingTask | None:
    cursor.execute(
        """
        SELECT
          t.*,
          COALESCE(kstats.current_snapshots, 0) AS current_snapshots
        FROM keyword_tracking_tasks t
        LEFT JOIN (
          SELECT
            k.marketplace,
            k.keyword,
            COUNT(DISTINCT krs.snapshot_at) AS current_snapshots
          FROM keywords k
          JOIN keyword_rank_snapshots krs ON krs.keyword_id = k.id
          GROUP BY k.marketplace, k.keyword
        ) kstats
          ON kstats.marketplace = t.marketplace
         AND kstats.keyword = t.keyword
        WHERE t.id = %s
        """,
        (task_id,),
    )
    row = cursor.fetchone()
    if row is None:
        if missing_ok:
            return None
        raise ValueError(f"关键词追踪任务不存在: {task_id}")
    return _row_to_task(row)


def _count_keyword_snapshot_times(cursor: Any, marketplace: str, keyword: str) -> int:
    cursor.execute(
        """
        SELECT COUNT(DISTINCT krs.snapshot_at) AS snapshot_count
        FROM keywords k
        JOIN keyword_rank_snapshots krs ON krs.keyword_id = k.id
        WHERE k.marketplace = %s
          AND k.keyword = %s
        """,
        (marketplace, keyword),
    )
    row = cursor.fetchone() or {}
    return int(_row_value(row, "snapshot_count", "SNAPSHOT_COUNT") or 0)


def _row_to_task(row: dict[str, Any]) -> KeywordTrackingTask:
    return KeywordTrackingTask(
        id=int(_row_value(row, "id", "ID")),
        marketplace=str(_row_value(row, "marketplace", "MARKETPLACE")),
        keyword=str(_row_value(row, "keyword", "KEYWORD")),
        target_snapshots=int(_row_value(row, "target_snapshots", "TARGET_SNAPSHOTS")),
        status=str(_row_value(row, "status", "STATUS")),
        pages_per_keyword=int(_row_value(row, "pages_per_keyword", "PAGES_PER_KEYWORD")),
        last_collected_at=_format_datetime(_row_value(row, "last_collected_at", "LAST_COLLECTED_AT", default=None)),
        last_checked_at=_format_datetime(_row_value(row, "last_checked_at", "LAST_CHECKED_AT", default=None)),
        achieved_snapshots=int(_row_value(row, "achieved_snapshots", "ACHIEVED_SNAPSHOTS") or 0),
        current_snapshots=int(_row_value(row, "current_snapshots", "CURRENT_SNAPSHOTS") or 0),
        error_message=_row_value(row, "error_message", "ERROR_MESSAGE", default=None),
        created_at=_format_datetime(_row_value(row, "created_at", "CREATED_AT", default=None)),
        updated_at=_format_datetime(_row_value(row, "updated_at", "UPDATED_AT", default=None)),
    )


def _format_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _normalize_marketplace(value: str) -> str:
    text = (value or "US").strip().upper()
    if not text:
        return "US"
    return text[:16]


def _normalize_keyword(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("关键词不能为空")
    return text


def _normalize_positive_int(value: int, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(number, maximum))


def _normalize_limit(value: int) -> int:
    return _normalize_positive_int(value, default=200, maximum=1000)


def normalize_tracking_pages_per_keyword(
    value: int | None,
    *,
    limits: CollectionLimits | None = None,
) -> int:
    """Normalize tracking page count with the user settings safety envelope."""

    effective_limits = limits or get_collection_limits()
    return _normalize_positive_int(
        value,
        default=effective_limits.pages_per_keyword,
        maximum=effective_limits.max_pages_per_keyword,
    )


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"无效任务状态: {status}. 可用状态: {', '.join(sorted(VALID_STATUSES))}")


def _row_value(row: dict[str, Any], *keys: str, default: Any = ...):
    for key in keys:
        if key in row:
            return row[key]
    lower_map = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        lowered = key.lower()
        if lowered in lower_map:
            return lower_map[lowered]
    if default is not ...:
        return default
    raise KeyError(keys[0])
