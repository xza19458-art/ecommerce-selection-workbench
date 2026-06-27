from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.task_center import IMPORT_JOB_URL_PREFIX, _normalize_row


def test_crawl_task_hides_import_metrics() -> None:
    row = _normalize_row(
        {
            "id": 1,
            "keyword": "toy dogs",
            "url": "https://www.amazon.com/s?k=toy+dogs",
            "pages": 2,
            "status": "完成",
            "started_at": "2026-06-24 23:12:02",
            "finished_at": "2026-06-24 23:12:40",
            "total_found": 32,
            "total_valid": 12,
            "total_inserted": 0,
            "error_message": None,
        }
    )

    assert row["job_type"] == "爬取"
    assert row["type"] == "爬取"
    assert row["valid_count"] is None
    assert row["ingested_count"] is None


def test_import_task_exposes_import_metrics_and_error_alias() -> None:
    row = _normalize_row(
        {
            "id": 2,
            "keyword": "toys",
            "url": f"{IMPORT_JOB_URL_PREFIX}html/toys/page_1.html",
            "pages": None,
            "status": "失败",
            "started_at": "2026-06-24 23:15:02",
            "finished_at": "2026-06-24 23:15:40",
            "total_found": 24,
            "total_valid": 18,
            "total_inserted": 16,
            "error_message": "数据库连接失败\nTraceback...",
        }
    )

    assert row["job_type"] == "入库"
    assert row["type"] == "入库"
    assert row["valid_count"] == 18
    assert row["ingested_count"] == 16
    assert row["error"] == "数据库连接失败\nTraceback..."
    assert row["failure_reason"] == "数据库连接失败\nTraceback..."


if __name__ == "__main__":
    tests = [
        test_crawl_task_hides_import_metrics,
        test_import_task_exposes_import_metrics_and_error_alias,
    ]
    for test in tests:
        test()
    print(f"task_center tests passed: {len(tests)}/{len(tests)}")
