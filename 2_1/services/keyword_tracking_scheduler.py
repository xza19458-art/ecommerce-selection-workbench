"""Queue engine for keyword tracking tasks.

C2 coordinates C1 keyword-tracking tasks with B1 collection and B2 ingestion.
It is not a daemon: callers invoke it manually on app startup or via an
"立即检查" action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from database.mysql_client import MySQLClient
from services.keyword_tracking import (
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    KeywordTrackingTask,
    list_tracking_tasks,
    record_tracking_collection,
    refresh_tracking_task_progress,
)
from services.snapshot_collection_runner import SnapshotCollectionRunSummary, run_snapshot_collection
from services.snapshot_storage import ingest_snapshot_html_and_sync_warehouse


MIN_TRACKING_INTERVAL_HOURS = 72


@dataclass(frozen=True)
class TrackingQueueDecision:
    task_id: int
    marketplace: str
    keyword: str
    target_snapshots: int
    current_snapshots: int
    status: str
    action: str
    reason: str
    last_collected_at: str | None
    pages_per_keyword: int
    collection_status: str | None = None
    saved_files: tuple[str, ...] = tuple()
    imported_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "任务ID": self.task_id,
            "站点": self.marketplace,
            "关键词": self.keyword,
            "目标快照数": self.target_snapshots,
            "当前快照数": self.current_snapshots,
            "任务状态": self.status,
            "动作": self.action,
            "原因": self.reason,
            "上次采集时间": self.last_collected_at,
            "每轮页数": self.pages_per_keyword,
            "采集状态": self.collection_status,
            "保存HTML": list(self.saved_files),
            "入库商品数": self.imported_count,
        }


@dataclass(frozen=True)
class TrackingSchedulerSummary:
    executed: bool
    started_at: datetime
    finished_at: datetime
    status: str
    decisions: tuple[TrackingQueueDecision, ...]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "executed": self.executed,
            "started_at": self.started_at.isoformat(sep=" "),
            "finished_at": self.finished_at.isoformat(sep=" "),
            "status": self.status,
            "message": self.message,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


def run_keyword_tracking_scheduler(
    *,
    execute: bool = False,
    task_id: int | None = None,
    limit: int = 20,
    min_interval_hours: int = MIN_TRACKING_INTERVAL_HOURS,
    save_root: str | Path = "html/tracking_snapshots",
    stop_file: str | Path = "runtime/stop_keyword_tracking.flag",
    manifest_root: str | Path = "数据结果/keyword_tracking_runs",
    client: MySQLClient | None = None,
) -> TrackingSchedulerSummary:
    """Check active tracking tasks and optionally execute due tasks serially."""

    db = client or MySQLClient()
    started_at = datetime.now().replace(microsecond=0)
    decisions: list[TrackingQueueDecision] = []
    tasks = _load_tasks(task_id=task_id, limit=limit, client=db)

    status = "完成"
    message = "关键词追踪检查完成。"
    for raw_task in tasks:
        task = refresh_tracking_task_progress(raw_task.id, client=db)
        decision = _decide_task(task, now=started_at, min_interval_hours=min_interval_hours)
        if decision.action != "queued":
            decisions.append(decision)
            continue

        if not execute:
            decisions.append(decision)
            continue

        executed_decision = _execute_tracking_task(
            task,
            min_interval_hours=min_interval_hours,
            save_root=save_root,
            stop_file=stop_file,
            manifest_root=manifest_root,
            client=db,
        )
        decisions.append(executed_decision)
        if executed_decision.action == "error":
            status = "异常停止"
            message = executed_decision.reason
            break

    if execute and not any(decision.action in {"collected", "error"} for decision in decisions):
        message = "没有到期任务需要采集。"
    elif not execute:
        message = "关键词追踪 dry-run 检查完成；未联网采集。"

    return TrackingSchedulerSummary(
        executed=execute,
        started_at=started_at,
        finished_at=datetime.now().replace(microsecond=0),
        status=status,
        decisions=tuple(decisions),
        message=message,
    )


def is_tracking_task_due(
    task: KeywordTrackingTask,
    *,
    now: datetime | None = None,
    min_interval_hours: int = MIN_TRACKING_INTERVAL_HOURS,
) -> tuple[bool, str]:
    """Return whether an active task should enter the collection queue."""

    current_time = now or datetime.now()
    if task.status != STATUS_ACTIVE:
        return False, f"任务状态为 {task.status}，不进入队列。"
    if task.current_snapshots >= task.target_snapshots:
        return False, f"已达到目标快照数 {task.current_snapshots}/{task.target_snapshots}。"
    last_collected_at = _parse_datetime(task.last_collected_at)
    if last_collected_at is None:
        return True, "未记录成功采集时间，且尚未达标，进入队列。"
    hours_since_last = max(0.0, (current_time - last_collected_at).total_seconds() / 3600)
    if hours_since_last < min_interval_hours:
        return False, f"距上次采集约 {hours_since_last:.1f} 小时，未达到 {min_interval_hours} 小时间隔。"
    return True, f"距上次采集约 {hours_since_last:.1f} 小时，已达到 {min_interval_hours} 小时间隔。"


def _load_tasks(*, task_id: int | None, limit: int, client: MySQLClient) -> list[KeywordTrackingTask]:
    if task_id is not None:
        task = refresh_tracking_task_progress(int(task_id), client=client)
        return [task]
    return list_tracking_tasks(status=STATUS_ACTIVE, limit=limit, client=client)


def _decide_task(task: KeywordTrackingTask, *, now: datetime, min_interval_hours: int) -> TrackingQueueDecision:
    due, reason = is_tracking_task_due(task, now=now, min_interval_hours=min_interval_hours)
    action = "queued" if due else ("completed" if task.status == STATUS_COMPLETED else "skipped")
    return TrackingQueueDecision(
        task_id=task.id,
        marketplace=task.marketplace,
        keyword=task.keyword,
        target_snapshots=task.target_snapshots,
        current_snapshots=task.current_snapshots,
        status=task.status,
        action=action,
        reason=reason,
        last_collected_at=task.last_collected_at,
        pages_per_keyword=task.pages_per_keyword,
    )


def _execute_tracking_task(
    task: KeywordTrackingTask,
    *,
    min_interval_hours: int,
    save_root: str | Path,
    stop_file: str | Path,
    manifest_root: str | Path,
    client: MySQLClient,
) -> TrackingQueueDecision:
    manifest_path = _build_manifest_path(manifest_root, task)
    collection = run_snapshot_collection(
        run=True,
        max_keywords=1,
        min_interval_hours=min_interval_hours,
        pages_per_keyword=task.pages_per_keyword,
        max_pages_per_keyword=task.pages_per_keyword,
        target_snapshots=task.target_snapshots,
        marketplace=task.marketplace,
        keyword=task.keyword,
        keyword_exact=True,
        save_root=save_root,
        stop_file=stop_file,
        manifest_path=manifest_path,
        ignore_interval=True,
        client=client,
    )
    saved_files = tuple(page.saved_file for page in collection.pages if page.status == "saved" and page.saved_file)
    if collection.status != "完成":
        updated = record_tracking_collection(
            task.id,
            error_message=f"{collection.status}: {collection.message}",
            client=client,
        )
        return _decision_from_error(task, updated, collection, saved_files)
    if not saved_files:
        refreshed = refresh_tracking_task_progress(task.id, client=client)
        return TrackingQueueDecision(
            task_id=refreshed.id,
            marketplace=refreshed.marketplace,
            keyword=refreshed.keyword,
            target_snapshots=refreshed.target_snapshots,
            current_snapshots=refreshed.current_snapshots,
            status=refreshed.status,
            action="skipped",
            reason="B1 runner 未生成可入库 HTML，可能未到期或无任务。",
            last_collected_at=refreshed.last_collected_at,
            pages_per_keyword=refreshed.pages_per_keyword,
            collection_status=collection.status,
        )

    ingest_summary = ingest_snapshot_html_and_sync_warehouse(
        saved_files,
        keyword=task.keyword,
        marketplace=task.marketplace,
        snapshot_at=collection.snapshot_at,
        pages=len(saved_files),
        url=_first_saved_url(collection),
        require_complete=True,
        client=client,
    )
    updated = record_tracking_collection(task.id, collected_at=collection.snapshot_at, client=client)
    return TrackingQueueDecision(
        task_id=updated.id,
        marketplace=updated.marketplace,
        keyword=updated.keyword,
        target_snapshots=updated.target_snapshots,
        current_snapshots=updated.current_snapshots,
        status=updated.status,
        action="collected",
        reason="采集、入库、仓库同步完成。",
        last_collected_at=updated.last_collected_at,
        pages_per_keyword=updated.pages_per_keyword,
        collection_status=collection.status,
        saved_files=saved_files,
        imported_count=ingest_summary.ingestion.total_inserted,
    )


def _decision_from_error(
    original: KeywordTrackingTask,
    updated: KeywordTrackingTask,
    collection: SnapshotCollectionRunSummary,
    saved_files: tuple[str, ...],
) -> TrackingQueueDecision:
    return TrackingQueueDecision(
        task_id=updated.id,
        marketplace=updated.marketplace or original.marketplace,
        keyword=updated.keyword or original.keyword,
        target_snapshots=updated.target_snapshots,
        current_snapshots=updated.current_snapshots,
        status=updated.status,
        action="error",
        reason=collection.message,
        last_collected_at=updated.last_collected_at,
        pages_per_keyword=updated.pages_per_keyword,
        collection_status=collection.status,
        saved_files=saved_files,
    )


def _first_saved_url(collection: SnapshotCollectionRunSummary) -> str | None:
    for page in collection.pages:
        if page.status == "saved":
            return page.url
    return None


def _build_manifest_path(root: str | Path, task: KeywordTrackingTask) -> Path:
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(root) / f"tracking_{task.id}_{_safe_name(task.keyword)}_{now}.json"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "keyword"
