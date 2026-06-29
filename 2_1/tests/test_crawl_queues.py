from __future__ import annotations

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import services.crawl_queues as q


def test_queue_persistence_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "crawl_queues.json"
        saved = q.save_queue(
            "队列1",
            [
                {"keyword": "a", "pages": 2, "collected_at": "2026-06-27 13:00:00"},
                {"keyword": "a", "pages": 9},   # 同词去重，保留首条
                {"keyword": " b ", "pages": 99},  # 去空格 + 页数钳到 7
                {"keyword": "", "pages": 1},      # 空词丢弃
            ],
            path=path,
        )
        assert saved["items"] == [
            {"keyword": "a", "pages": 2, "collected_at": "2026-06-27 13:00:00"},
            {"keyword": "b", "pages": 7, "collected_at": ""},
        ]
        # collected_at 往返保留：复用队列能知道该词何时采过
        assert q.get_queue("队列1", path=path)["items"][0]["collected_at"] == "2026-06-27 13:00:00"

        q.save_queue("队列2", [{"keyword": "c", "pages": 1}], path=path)
        assert sorted(x["name"] for x in q.list_queues(path=path)) == ["队列1", "队列2"]

        # 同名 upsert：整列覆盖
        q.save_queue("队列1", [{"keyword": "z", "pages": 3}], path=path)
        assert q.get_queue("队列1", path=path)["items"] == [{"keyword": "z", "pages": 3, "collected_at": ""}]

        assert q.delete_queue("队列2", path=path) is True
        assert q.get_queue("队列2", path=path) is None
        assert q.delete_queue("不存在", path=path) is False


def test_empty_name_rejected() -> None:
    try:
        q.save_queue("  ", [], path=None)
        raise AssertionError("空队列名应报错")
    except q.CrawlQueueError:
        pass


def test_crawl_and_import_outcome_classification() -> None:
    from core.controller import AppController
    import services.ingestion as ingestion

    class _Summary:
        total_inserted = 5

    controller = AppController()
    original_ingest = ingestion.ingest_html_files_to_mysql
    ingestion.ingest_html_files_to_mysql = lambda *a, **k: _Summary()
    try:
        # 完成 + 入库
        controller.run_keyword_crawl = lambda kw, pages=None, record_job=True: {
            "状态": "完成", "保存目录": "html/x", "message": "ok",
            "页面": [{"状态": "已保存", "保存文件": "html/x/x_1.html", "原因": ""}],
        }
        done = controller.run_keyword_crawl_and_import("x", 1)
        assert done["outcome"] == "完成" and done["入库商品数"] == 5

        # 被拦（任意页 blocked）→ 被拦
        controller.run_keyword_crawl = lambda *a, **k: {
            "状态": "异常停止", "message": "被拦",
            "页面": [{"状态": "blocked", "原因": "验证码"}],
        }
        assert controller.run_keyword_crawl_and_import("x", 1)["outcome"] == "被拦"

        # 完成但 0 有效保存 → 未采到
        controller.run_keyword_crawl = lambda *a, **k: {
            "状态": "完成", "message": "空", "页面": [{"状态": "empty", "原因": "空页"}],
        }
        assert controller.run_keyword_crawl_and_import("x", 1)["outcome"] == "未采到"

        # 异常停止（无 blocked）→ 失败
        controller.run_keyword_crawl = lambda *a, **k: {"状态": "异常停止", "message": "超时", "页面": []}
        assert controller.run_keyword_crawl_and_import("x", 1)["outcome"] == "失败"

        # 入库抛错 → 失败 + 原因带"入库失败"
        ingestion.ingest_html_files_to_mysql = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
        controller.run_keyword_crawl = lambda *a, **k: {
            "状态": "完成", "message": "ok",
            "页面": [{"状态": "已保存", "保存文件": "html/x/x_1.html"}],
        }
        failed = controller.run_keyword_crawl_and_import("x", 1)
        assert failed["outcome"] == "失败" and "入库失败" in failed["原因"]
    finally:
        ingestion.ingest_html_files_to_mysql = original_ingest


if __name__ == "__main__":
    tests = [
        test_queue_persistence_roundtrip,
        test_empty_name_rejected,
        test_crawl_and_import_outcome_classification,
    ]
    for test in tests:
        test()
    print(f"crawl_queues tests passed: {len(tests)}/{len(tests)}")
