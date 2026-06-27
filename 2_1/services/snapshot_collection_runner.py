"""Manual low-frequency snapshot collection runner.

The runner is the second step after the dry-run planner. It may open Amazon
search pages only when explicitly requested by the CLI. It saves HTML files and
job logs, but does not import product data; Storage's B2 tool handles MySQL
snapshot ingestion and warehouse sync from the saved HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Any

from bs4 import BeautifulSoup

from database.mysql_client import MySQLClient
from parsers.amazon_search_parser import parse_amazon_search_content
from services.snapshot_collection_plan import (
    SnapshotCollectionPlan,
    SnapshotCollectionTask,
    build_snapshot_collection_plan,
)
from services.settings import CollectionLimits, get_collection_limits


MAX_KEYWORDS_PER_RUN = 3
MAX_PAGES_PER_KEYWORD = 7  # 2026-06-18 人类裁定调高至 7（Amazon 搜索常见到底页数），见 decisions/2026-06-18-限频边界调整.md
MIN_INTERVAL_HOURS = 72
MIN_PAGE_DELAY_SECONDS = 5  # 2026-06-18 对齐原有 crawl_amazon 实证节奏（页间 3-5s），效率/稳定折中，见 decisions/2026-06-18-限频边界调整.md
MAX_RUNTIME_MINUTES = 120   # 默认 2 小时；S 系列设置允许用户调高，不再作为硬上限。

BLOCKED_DIR_NAME = "_blocked"

BLOCKED_PATTERNS = (
    r"captcha",
    r"robot\s+check",
    r"enter\s+the\s+characters",
    r"type\s+the\s+characters",
    r"automated\s+access",
    r"unusual\s+traffic",
    r"validatecaptcha",
    r"we\s+just\s+need\s+to\s+make\s+sure\s+you'?re\s+not\s+a\s+robot",
)


@dataclass(frozen=True)
class PageCollectionResult:
    keyword: str
    marketplace: str
    page_no: int
    url: str
    status: str
    saved_file: str | None
    total_found: int
    total_valid: int
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "关键词": self.keyword,
            "站点": self.marketplace,
            "页码": self.page_no,
            "URL": self.url,
            "状态": self.status,
            "保存文件": self.saved_file,
            "解析商品数": self.total_found,
            "有效商品数": self.total_valid,
            "原因": self.reason,
        }


@dataclass(frozen=True)
class SnapshotCollectionRunSummary:
    dry_run: bool
    status: str
    started_at: datetime
    finished_at: datetime
    snapshot_at: datetime
    plan: SnapshotCollectionPlan
    pages: tuple[PageCollectionResult, ...]
    manifest_path: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "status": self.status,
            "started_at": self.started_at.isoformat(sep=" "),
            "finished_at": self.finished_at.isoformat(sep=" "),
            "snapshot_at": self.snapshot_at.isoformat(sep=" "),
            "message": self.message,
            "manifest_path": self.manifest_path,
            "plan_summary": self.plan.summary(),
            "plan_tasks": [
                {
                    **task.to_dict(),
                    "页面": [page.to_dict() for page in task.pages],
                }
                for task in self.plan.tasks
            ],
            "pages": [page.to_dict() for page in self.pages],
        }


def run_snapshot_collection(
    *,
    run: bool = False,
    max_keywords: int = MAX_KEYWORDS_PER_RUN,
    min_interval_hours: int | None = None,
    pages_per_keyword: int | None = None,
    max_pages_per_keyword: int | None = None,
    target_snapshots: int = 3,
    marketplace: str | None = None,
    keyword: str | None = None,
    keyword_exact: bool = False,
    seed_keyword: bool = False,
    save_root: str | Path = "html/snapshots",
    stop_file: str | Path = "runtime/stop_snapshot_collection.flag",
    page_delay_min_seconds: int | None = None,
    page_delay_max_seconds: int | None = None,
    max_runtime_minutes: int | None = None,
    manifest_path: str | Path | None = None,
    record_jobs: bool = True,
    ignore_interval: bool = False,
    client: MySQLClient | None = None,
    controller: Any | None = None,
) -> SnapshotCollectionRunSummary:
    """Run or preview a manual low-frequency snapshot collection round.

    ignore_interval=True 仅供人工显式补采时豁免 72h 间隔（如刚采过但要按新页数上限
    补齐）；默认 False 仍严守 72h 硬下限。其余限频硬上限不受影响。

    seed_keyword=True：冷启动播种，给还没商品池的关键词也排首采任务（追踪用）。

    controller：传入则复用该浏览器会话且**结束不关闭**（生命周期归调用方，复用已暖
    会话）；不传则本函数自建一次性 controller 并在结束时关闭（CLI 路径，向后兼容）。
    """

    started_at = datetime.now().replace(microsecond=0)
    snapshot_at = started_at.replace(minute=0, second=0, microsecond=0)
    limits = _normalize_runner_limits(
        max_keywords=max_keywords,
        min_interval_hours=min_interval_hours,
        pages_per_keyword=pages_per_keyword,
        max_pages_per_keyword=max_pages_per_keyword,
        page_delay_min_seconds=page_delay_min_seconds,
        page_delay_max_seconds=page_delay_max_seconds,
        max_runtime_minutes=max_runtime_minutes,
    )
    plan = build_snapshot_collection_plan(
        max_keywords=limits["max_keywords"],
        min_interval_hours=0 if ignore_interval else limits["min_interval_hours"],
        default_pages=limits["pages_per_keyword"],
        max_pages_per_keyword=limits["max_pages_per_keyword"],
        target_snapshots=target_snapshots,
        marketplace=marketplace,
        keyword=keyword,
        keyword_exact=keyword_exact,
        seed_keyword=seed_keyword,
        save_root=save_root,
        now=snapshot_at,
        client=client,
    )

    if not run:
        summary = SnapshotCollectionRunSummary(
            dry_run=True,
            status="dry-run",
            started_at=started_at,
            finished_at=datetime.now().replace(microsecond=0),
            snapshot_at=snapshot_at,
            plan=plan,
            pages=tuple(),
            manifest_path=str(manifest_path) if manifest_path else None,
            message="dry-run 预览完成；未联网、未写库、未保存 HTML。",
        )
        _write_manifest(summary, manifest_path)
        return summary

    if not plan.tasks:
        summary = SnapshotCollectionRunSummary(
            dry_run=False,
            status="完成",
            started_at=started_at,
            finished_at=datetime.now().replace(microsecond=0),
            snapshot_at=snapshot_at,
            plan=plan,
            pages=tuple(),
            manifest_path=str(manifest_path) if manifest_path else None,
            message="暂无到期关键词，未打开浏览器。",
        )
        _write_manifest(summary, manifest_path)
        return summary

    db = client or MySQLClient()
    # 传入 controller 则复用其已暖会话、结束不关；未传则自建一次性 controller 并在结束关闭。
    owns_controller = controller is None
    if owns_controller:
        controller = _build_controller()
    page_results: list[PageCollectionResult] = []
    status = "完成"
    message = "采集完成；已保存 HTML。后续入库请使用 B2 的 import_snapshots_and_sync.py。"
    started_monotonic = time.monotonic()
    stop_path = Path(stop_file)

    try:
        for task_index, task in enumerate(plan.tasks):
            if _should_stop(stop_path):
                status = "用户停止"
                message = f"检测到 stop flag，停止本轮: {stop_path}"
                break
            if _runtime_exceeded(started_monotonic, limits["max_runtime_minutes"]):
                status = "异常停止"
                message = f"达到单轮最长运行时间 {limits['max_runtime_minutes']} 分钟，停止本轮。"
                break

            task_results, task_status, task_message = _collect_task(
                controller,
                task,
                db=db,
                snapshot_at=snapshot_at,
                stop_file=stop_path,
                started_monotonic=started_monotonic,
                max_runtime_minutes=limits["max_runtime_minutes"],
                page_delay_min_seconds=limits["page_delay_min_seconds"],
                page_delay_max_seconds=limits["page_delay_max_seconds"],
                should_delay_before_first_page=task_index > 0,
                record_jobs=record_jobs,
            )
            page_results.extend(task_results)
            if task_status != "完成":
                status = task_status
                message = task_message
                break
    finally:
        if owns_controller:
            controller.stop_browser()

    summary = SnapshotCollectionRunSummary(
        dry_run=False,
        status=status,
        started_at=started_at,
        finished_at=datetime.now().replace(microsecond=0),
        snapshot_at=snapshot_at,
        plan=plan,
        pages=tuple(page_results),
        manifest_path=str(manifest_path) if manifest_path else None,
        message=message,
    )
    _write_manifest(summary, manifest_path)
    return summary


def classify_amazon_search_page(html: str, *, current_url: str = "", title: str = "") -> tuple[str, str | None]:
    """Classify fetched Amazon search HTML as ok / blocked / empty."""

    lowered_url = current_url.lower()
    lowered_title = title.lower()
    text = _visible_text(html).lower()
    if "/ap/signin" in lowered_url or "amazon sign-in" in lowered_title or "amazon sign in" in lowered_title:
        return "blocked", "页面跳转到登录页"
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, lowered_url) or re.search(pattern, lowered_title) or re.search(pattern, text):
            return "blocked", f"检测到阻断内容: {pattern}"
    if _count_search_result_nodes(html) <= 0:
        return "empty", "页面没有搜索结果节点"
    return "ok", None


def _collect_task(
    controller: Any,
    task: SnapshotCollectionTask,
    *,
    db: MySQLClient,
    snapshot_at: datetime,
    stop_file: Path,
    started_monotonic: float,
    max_runtime_minutes: int,
    page_delay_min_seconds: int,
    page_delay_max_seconds: int,
    should_delay_before_first_page: bool,
    record_jobs: bool,
) -> tuple[list[PageCollectionResult], str, str]:
    results: list[PageCollectionResult] = []
    job_id: int | None = None
    if record_jobs:
        job_id = _create_collection_job(db, task)

    try:
        stop_message: str | None = None
        stop_status = "异常停止"

        def handle_page(page_num: int, html: str, current_url: str, title: str) -> bool:
            nonlocal stop_message, stop_status
            if _should_stop(stop_file):
                message = f"检测到 stop flag，停止本轮: {stop_file}"
                stop_message = message
                stop_status = "用户停止"
                return False
            if _runtime_exceeded(started_monotonic, max_runtime_minutes):
                message = f"达到单轮最长运行时间 {max_runtime_minutes} 分钟，停止本轮。"
                stop_message = message
                stop_status = "异常停止"
                return False

            page_index = max(0, min(page_num - 1, len(task.pages) - 1))
            page = task.pages[page_index]
            result = _collect_page_html(
                task,
                page,
                html,
                current_url=current_url,
                title=title,
                snapshot_at=snapshot_at,
            )
            results.append(result)
            if result.status in {"blocked", "empty", "failed"}:
                stop_message = result.reason or "采集异常，已停止本轮。"
                stop_status = "异常停止"
                return False
            return True

        if should_delay_before_first_page:
            _sleep_between_pages(page_delay_min_seconds, page_delay_max_seconds)

        if task.pages:
            controller.collect_amazon_search_pages(
                task.pages[0].url,
                pages=len(task.pages),
                on_page=handle_page,
                stop_requested=lambda: _should_stop(stop_file)
                or _runtime_exceeded(started_monotonic, max_runtime_minutes),
                page_delay_seconds=(page_delay_min_seconds, page_delay_max_seconds),
            )

        if stop_message:
            _finish_collection_job(db, job_id, stop_status, results, stop_message)
            return results, stop_status, stop_message

        _finish_collection_job(db, job_id, "完成", results, None)
        return results, "完成", "关键词采集完成。"
    except Exception as exc:
        message = f"采集失败: {exc}"
        _finish_collection_job(db, job_id, "失败", results, message)
        return results, "失败", message


def _collect_page_html(
    task: SnapshotCollectionTask,
    page: Any,
    html: str,
    *,
    current_url: str,
    title: str,
    snapshot_at: datetime,
) -> PageCollectionResult:
    state, reason = classify_amazon_search_page(html, current_url=current_url, title=title)

    if state == "blocked":
        saved_file = _save_blocked_html(task, page.page_no, html, snapshot_at, "blocked")
        return PageCollectionResult(
            keyword=task.keyword,
            marketplace=task.marketplace,
            page_no=page.page_no,
            url=current_url,
            status="blocked",
            saved_file=str(saved_file),
            total_found=0,
            total_valid=0,
            reason=reason,
        )

    if state == "empty":
        saved_file = _save_blocked_html(task, page.page_no, html, snapshot_at, "empty")
        return PageCollectionResult(
            keyword=task.keyword,
            marketplace=task.marketplace,
            page_no=page.page_no,
            url=current_url,
            status="empty",
            saved_file=str(saved_file),
            total_found=0,
            total_valid=0,
            reason=reason,
        )

    parse_result = parse_amazon_search_content(
        html,
        source_file=str(page.suggested_file),
        keyword=task.keyword,
        marketplace=task.marketplace,
        snapshot_at=snapshot_at,
        require_complete=True,
    )
    if parse_result.total_valid <= 0:
        saved_file = _save_blocked_html(task, page.page_no, html, snapshot_at, "empty")
        return PageCollectionResult(
            keyword=task.keyword,
            marketplace=task.marketplace,
            page_no=page.page_no,
            url=current_url,
            status="empty",
            saved_file=str(saved_file),
            total_found=parse_result.total_found,
            total_valid=parse_result.total_valid,
            reason="页面存在结果节点，但严格解析后有效商品为 0，停止本轮避免写入噪声。",
        )

    output = Path(page.suggested_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return PageCollectionResult(
        keyword=task.keyword,
        marketplace=task.marketplace,
        page_no=page.page_no,
        url=current_url,
        status="saved",
        saved_file=str(output),
        total_found=parse_result.total_found,
        total_valid=parse_result.total_valid,
        reason=None,
    )


def _build_controller() -> Any:
    from core.controller import AppController

    return AppController()


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return soup.get_text(" ", strip=True)


def _count_search_result_nodes(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    return len(soup.select("div[data-component-type='s-search-result'][data-asin]"))


def _save_blocked_html(
    task: SnapshotCollectionTask,
    page_no: int,
    html: str,
    snapshot_at: datetime,
    suffix: str,
) -> Path:
    base = Path(task.save_dir).parent.parent / BLOCKED_DIR_NAME / snapshot_at.strftime("%Y%m%d_%H%M")
    base.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_name(task.keyword)}_p{page_no}_{suffix}_{snapshot_at.strftime('%Y%m%d_%H%M')}.html"
    path = base / filename
    path.write_text(html, encoding="utf-8")
    return path


def _create_collection_job(db: MySQLClient, task: SnapshotCollectionTask) -> int:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            return db.create_job(
                cursor,
                task.keyword,
                task.pages[0].url if task.pages else None,
                task.recommended_pages,
            )


def _finish_collection_job(
    db: MySQLClient,
    job_id: int | None,
    status: str,
    results: list[PageCollectionResult],
    error_message: str | None,
) -> None:
    if job_id is None:
        return
    with db.connect() as conn:
        with conn.cursor() as cursor:
            db.finish_job(
                cursor,
                job_id,
                status,
                total_found=sum(result.total_found for result in results),
                total_valid=sum(result.total_valid for result in results),
                total_inserted=0,
                error_message=error_message,
            )


def _write_manifest(summary: SnapshotCollectionRunSummary, manifest_path: str | Path | None) -> None:
    if manifest_path is None:
        return
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = summary.to_dict()
    payload["manifest_path"] = str(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sleep_between_pages(min_seconds: int, max_seconds: int) -> None:
    duration = min_seconds if max_seconds <= min_seconds else (min_seconds + max_seconds) / 2
    time.sleep(duration)


def _should_stop(stop_file: Path) -> bool:
    return stop_file.exists()


def _runtime_exceeded(started_monotonic: float, max_runtime_minutes: int) -> bool:
    return (time.monotonic() - started_monotonic) >= max_runtime_minutes * 60


def _normalize_runner_limits(
    *,
    collection_limits: CollectionLimits | None = None,
    **kwargs: int | None,
) -> dict[str, int]:
    settings_limits = collection_limits or get_collection_limits()
    max_keywords = _clamp(kwargs["max_keywords"], 1, MAX_KEYWORDS_PER_RUN)
    min_interval_hours = max(
        settings_limits.tracking_min_interval_hours,
        _int_value(kwargs["min_interval_hours"], settings_limits.tracking_min_interval_hours),
    )
    max_pages_per_keyword = _clamp(
        _int_value(kwargs["max_pages_per_keyword"], settings_limits.max_pages_per_keyword),
        1,
        settings_limits.max_pages_per_keyword,
    )
    pages_per_keyword = _clamp(
        _int_value(kwargs["pages_per_keyword"], settings_limits.pages_per_keyword),
        1,
        max_pages_per_keyword,
    )
    if pages_per_keyword > max_pages_per_keyword:
        pages_per_keyword = max_pages_per_keyword
    page_delay_min_seconds = max(
        MIN_PAGE_DELAY_SECONDS,
        _int_value(kwargs["page_delay_min_seconds"], settings_limits.page_delay_min_seconds),
    )
    page_delay_max_seconds = max(
        page_delay_min_seconds,
        _int_value(kwargs["page_delay_max_seconds"], settings_limits.page_delay_max_seconds),
    )
    max_runtime_minutes = _positive_int(
        kwargs["max_runtime_minutes"],
        default=settings_limits.max_runtime_minutes,
    )
    return {
        "max_keywords": max_keywords,
        "min_interval_hours": min_interval_hours,
        "pages_per_keyword": pages_per_keyword,
        "max_pages_per_keyword": max_pages_per_keyword,
        "page_delay_min_seconds": page_delay_min_seconds,
        "page_delay_max_seconds": page_delay_max_seconds,
        "max_runtime_minutes": max_runtime_minutes,
    }


def _clamp(value: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(number, maximum))


def _int_value(value: int | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value: int | None, *, default: int) -> int:
    number = _int_value(value, default)
    return max(1, number)


def _safe_name(value: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", value.strip(), flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "keyword"
