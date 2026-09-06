from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.task_center import (
    IMPORT_JOB_URL_PREFIX,
    _build_filters,
    _normalize_row,
    fetch_task_jobs_page,
)


class _FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []
        self._rows = [
            {
                "id": 7,
                "keyword": "squishy",
                "url": "https://www.amazon.com/s?k=squishy",
                "pages": 2,
                "status": "完成",
                "started_at": "2026-07-29 10:00:00",
                "finished_at": "2026-07-29 10:01:00",
                "total_found": 40,
                "total_valid": 35,
                "total_inserted": 0,
                "error_message": None,
            }
        ]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql, params):
        self.calls.append((" ".join(str(sql).split()), list(params)))

    def fetchone(self):
        return {"total": 31}

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self._cursor


class _FakeClient:
    def __init__(self) -> None:
        self.cursor = _FakeCursor()

    def connect(self):
        return _FakeConnection(self.cursor)


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


def test_task_filters_distinguish_crawl_and_import() -> None:
    crawl_sql, crawl_params = _build_filters(
        keyword="squishy",
        status="完成",
        job_type="crawl",
    )
    import_sql, import_params = _build_filters(job_type="import")

    assert "COALESCE(keyword, '') LIKE %s" in crawl_sql
    assert "status = %s" in crawl_sql
    assert "NOT (COALESCE(url, '') LIKE %s" in crawl_sql
    assert crawl_params == ["%squishy%", "完成", f"{IMPORT_JOB_URL_PREFIX}%"]
    assert "NOT" not in import_sql
    assert import_params == [f"{IMPORT_JOB_URL_PREFIX}%"]


def test_task_page_returns_total_and_paging_metadata() -> None:
    client = _FakeClient()

    page = fetch_task_jobs_page(
        limit=25,
        offset=25,
        keyword="squishy",
        status="完成",
        job_type="crawl",
        client=client,
    )

    assert page["total"] == 31
    assert page["limit"] == 25
    assert page["offset"] == 25
    assert page["rows"][0]["job_type"] == "爬取"
    assert len(client.cursor.calls) == 2
    assert client.cursor.calls[1][1][-2:] == [25, 25]


if __name__ == "__main__":
    tests = [
        test_crawl_task_hides_import_metrics,
        test_import_task_exposes_import_metrics_and_error_alias,
        test_task_filters_distinguish_crawl_and_import,
        test_task_page_returns_total_and_paging_metadata,
    ]
    for test in tests:
        test()
    print(f"task_center tests passed: {len(tests)}/{len(tests)}")
