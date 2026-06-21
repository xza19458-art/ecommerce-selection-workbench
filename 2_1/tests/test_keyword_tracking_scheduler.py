from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.keyword_tracking import KeywordTrackingTask
from services.keyword_tracking_scheduler import is_tracking_task_due


def _task(**overrides) -> KeywordTrackingTask:
    data = {
        "id": 1,
        "marketplace": "US",
        "keyword": "gift for man",
        "target_snapshots": 3,
        "status": "active",
        "pages_per_keyword": 2,
        "last_collected_at": None,
        "last_checked_at": None,
        "achieved_snapshots": 1,
        "current_snapshots": 1,
        "error_message": None,
        "created_at": None,
        "updated_at": None,
    }
    data.update(overrides)
    return KeywordTrackingTask(**data)


def test_completed_snapshot_count_is_not_due() -> None:
    due, reason = is_tracking_task_due(_task(current_snapshots=3, achieved_snapshots=3))
    assert due is False
    assert "已达到目标" in reason


def test_never_collected_active_task_is_due() -> None:
    due, reason = is_tracking_task_due(_task())
    assert due is True
    assert "未记录成功采集时间" in reason


def test_recent_collection_is_not_due() -> None:
    now = datetime(2026, 6, 19, 12, 0, 0)
    due, reason = is_tracking_task_due(
        _task(last_collected_at="2026-06-18 12:00:00"),
        now=now,
        min_interval_hours=72,
    )
    assert due is False
    assert "未达到 72" in reason


def test_old_collection_is_due() -> None:
    now = datetime(2026, 6, 19, 12, 0, 0)
    due, reason = is_tracking_task_due(
        _task(last_collected_at="2026-06-15 11:00:00"),
        now=now,
        min_interval_hours=72,
    )
    assert due is True
    assert "已达到 72" in reason


if __name__ == "__main__":
    tests = [
        test_completed_snapshot_count_is_not_due,
        test_never_collected_active_task_is_due,
        test_recent_collection_is_not_due,
        test_old_collection_is_due,
    ]
    for test in tests:
        test()
    print(f"keyword_tracking_scheduler tests passed: {len(tests)}/{len(tests)}")
