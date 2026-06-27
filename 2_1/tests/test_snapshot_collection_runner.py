from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.snapshot_collection_plan import (
    SnapshotCollectionPage,
    SnapshotCollectionTask,
    build_snapshot_collection_plan,
)
from services.settings import get_collection_limits
from services import snapshot_collection_runner
from services.snapshot_collection_runner import (
    _collect_task,
    _normalize_runner_limits,
    classify_amazon_search_page,
    run_snapshot_collection,
)


def test_captcha_is_blocked() -> None:
    html = "<html><body>Sorry, we just need to make sure you're not a robot. CAPTCHA</body></html>"
    state, reason = classify_amazon_search_page(html, current_url="https://www.amazon.com/errors/validateCaptcha")
    assert state == "blocked"
    assert reason


def test_signin_is_blocked() -> None:
    html = "<html><body>Amazon Sign-In</body></html>"
    state, reason = classify_amazon_search_page(html, current_url="https://www.amazon.com/ap/signin", title="Amazon Sign-In")
    assert state == "blocked"
    assert reason == "页面跳转到登录页"


def test_empty_search_is_empty() -> None:
    html = "<html><body><h1>Results</h1></body></html>"
    state, reason = classify_amazon_search_page(html)
    assert state == "empty"
    assert reason == "页面没有搜索结果节点"


def test_search_result_is_ok() -> None:
    html = """
    <html>
      <body>
        <div data-component-type="s-search-result" data-asin="B012345678">
          <h2><span>Example product</span></h2>
        </div>
      </body>
    </html>
    """
    state, reason = classify_amazon_search_page(html, current_url="https://www.amazon.com/s?k=test")
    assert state == "ok"
    assert reason is None


def test_collect_task_uses_controller_search_flow() -> None:
    class FakeController:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def collect_amazon_search_pages(self, url, pages, on_page, stop_requested, page_delay_seconds):
            self.calls.append(
                {
                    "url": url,
                    "pages": pages,
                    "page_delay_seconds": page_delay_seconds,
                    "stop_requested": bool(stop_requested()),
                }
            )
            on_page(
                1,
                "<html><body><h1>Results</h1></body></html>",
                "https://www.amazon.com/s?k=test",
                "Amazon.com: test",
            )

    with tempfile.TemporaryDirectory() as tmp_dir:
        save_dir = Path(tmp_dir) / "snapshots" / "US" / "test"
        pages = (
            SnapshotCollectionPage(
                page_no=1,
                url="https://www.amazon.com/s?k=test",
                suggested_file=str(save_dir / "test_p1.html"),
            ),
            SnapshotCollectionPage(
                page_no=2,
                url="https://www.amazon.com/s?k=test&page=2",
                suggested_file=str(save_dir / "test_p2.html"),
            ),
        )
        task = SnapshotCollectionTask(
            priority=1,
            keyword_id=1,
            marketplace="US",
            keyword="test",
            tracked_products=1,
            avg_snapshots_per_product=0,
            min_snapshots_per_product=0,
            max_score=None,
            last_collected_at=None,
            hours_since_last=None,
            recommended_pages=2,
            reason="unit test",
            save_dir=str(save_dir),
            pages=pages,
        )
        controller = FakeController()

        results, status, message = _collect_task(
            controller,
            task,
            db=object(),
            snapshot_at=datetime(2026, 6, 19, 10, 0, 0),
            stop_file=Path(tmp_dir) / "stop.flag",
            started_monotonic=time.monotonic(),
            max_runtime_minutes=1,
            page_delay_min_seconds=5,
            page_delay_max_seconds=5,
            should_delay_before_first_page=False,
            record_jobs=False,
        )

    assert controller.calls == [
        {
            "url": "https://www.amazon.com/s?k=test",
            "pages": 2,
            "page_delay_seconds": (5, 5),
            "stop_requested": False,
        }
    ]
    assert status == "异常停止"
    assert message == "页面没有搜索结果节点"
    assert len(results) == 1
    assert results[0].status == "empty"


def test_runner_limits_use_settings_defaults_and_hard_clamps() -> None:
    limits = get_collection_limits(
        {
            "collection": {
                "page_delay_min_seconds": 8,
                "page_delay_max_seconds": 12,
                "pages_per_keyword": 4,
                "max_pages_per_keyword": 5,
                "tracking_min_interval_hours": 96,
                "snapshot_expire_days": 3,
                "max_runtime_minutes": 9999,
            }
        }
    )

    defaults = _normalize_runner_limits(
        collection_limits=limits,
        max_keywords=1,
        min_interval_hours=None,
        pages_per_keyword=None,
        max_pages_per_keyword=None,
        page_delay_min_seconds=None,
        page_delay_max_seconds=None,
        max_runtime_minutes=None,
    )
    clamped = _normalize_runner_limits(
        collection_limits=limits,
        max_keywords=99,
        min_interval_hours=1,
        pages_per_keyword=99,
        max_pages_per_keyword=99,
        page_delay_min_seconds=1,
        page_delay_max_seconds=2,
        max_runtime_minutes=9999,
    )

    assert defaults["pages_per_keyword"] == 4
    assert defaults["max_pages_per_keyword"] == 5
    assert defaults["min_interval_hours"] == 96
    assert defaults["page_delay_min_seconds"] == 8
    assert defaults["page_delay_max_seconds"] == 12
    assert defaults["max_runtime_minutes"] == 9999
    assert clamped["max_keywords"] == 3
    assert clamped["min_interval_hours"] == 96
    assert clamped["pages_per_keyword"] == 5
    assert clamped["page_delay_min_seconds"] == 5
    assert clamped["page_delay_max_seconds"] == 5
    assert clamped["max_runtime_minutes"] == 9999


# ---------- 关键词追踪冷启动 + 持久会话（本波新增） ----------


class _FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return _FakeCursor()


class _FakeClientNoCandidates:
    """模拟没有任何已有关键词商品池的 MySQL（候选查询返回空）。"""

    def connect(self):
        return _FakeConn()


class _LifecycleController:
    """记录 stop_browser 是否被调用；采集返回空结果页即可触发收尾。"""

    def __init__(self) -> None:
        self.stopped = False

    def collect_amazon_search_pages(self, url, pages, on_page=None, stop_requested=None, page_delay_seconds=(5, 5)):
        if on_page:
            on_page(1, "<html><body><h1>Results</h1></body></html>", url, "Amazon.com")
        return []

    def stop_browser(self) -> None:
        self.stopped = True


def test_seed_keyword_plans_brand_new_keyword() -> None:
    plan = build_snapshot_collection_plan(
        keyword="brand new gizmo",
        marketplace="US",
        keyword_exact=True,
        seed_keyword=True,
        client=_FakeClientNoCandidates(),
    )
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.keyword == "brand new gizmo"
    assert task.pages
    assert "k=brand+new+gizmo" in task.pages[0].url


def test_no_seed_without_flag_keeps_plan_empty() -> None:
    plan = build_snapshot_collection_plan(
        keyword="brand new gizmo",
        marketplace="US",
        keyword_exact=True,
        seed_keyword=False,
        client=_FakeClientNoCandidates(),
    )
    assert plan.tasks == ()


def test_external_controller_is_reused_and_not_closed() -> None:
    controller = _LifecycleController()
    with tempfile.TemporaryDirectory() as tmp_dir:
        summary = run_snapshot_collection(
            run=True,
            keyword="brand new gizmo",
            marketplace="US",
            keyword_exact=True,
            seed_keyword=True,
            record_jobs=False,
            save_root=str(Path(tmp_dir) / "snapshots"),
            stop_file=str(Path(tmp_dir) / "stop.flag"),
            manifest_path=None,
            client=_FakeClientNoCandidates(),
            controller=controller,
        )
    assert summary.dry_run is False
    # 外部 controller：生命周期归调用方，采完不关，会话可复用。
    assert controller.stopped is False


def test_owned_controller_is_closed() -> None:
    built = _LifecycleController()
    original = snapshot_collection_runner._build_controller
    snapshot_collection_runner._build_controller = lambda: built
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_snapshot_collection(
                run=True,
                keyword="brand new gizmo",
                marketplace="US",
                keyword_exact=True,
                seed_keyword=True,
                record_jobs=False,
                save_root=str(Path(tmp_dir) / "snapshots"),
                stop_file=str(Path(tmp_dir) / "stop.flag"),
                manifest_path=None,
                client=_FakeClientNoCandidates(),
            )
    finally:
        snapshot_collection_runner._build_controller = original
    # 自建 controller：结束时关闭，向后兼容 CLI 一次性路径。
    assert built.stopped is True


if __name__ == "__main__":
    tests = [
        test_captcha_is_blocked,
        test_signin_is_blocked,
        test_empty_search_is_empty,
        test_search_result_is_ok,
        test_collect_task_uses_controller_search_flow,
        test_runner_limits_use_settings_defaults_and_hard_clamps,
        test_seed_keyword_plans_brand_new_keyword,
        test_no_seed_without_flag_keeps_plan_empty,
        test_external_controller_is_reused_and_not_closed,
        test_owned_controller_is_closed,
    ]
    for test in tests:
        test()
    print(f"snapshot_collection_runner tests passed: {len(tests)}/{len(tests)}")
