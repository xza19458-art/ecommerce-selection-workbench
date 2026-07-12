"""FastAPI API 层（D1）——把现有 `AppController` 的只读查询暴露为 REST 端点。

设计（见 decisions/2026-06-19-前端架构转Web.md §6）：
- 后端链路完全不变；本层只是新的"调用方"，把 controller 方法包成 HTTP。
- 本期只做 **GET 查询类**（安全、无写/联网）。写/联网类端点（建追踪任务、触发采集）
  后续单独加，且必须照守既有边界，不在本层绕过。
- 统一返回 `{"ok": bool, "data": ..., "message": str}`；异常兜底为 500 + message。

本地启动：
    uvicorn api.app:app --reload --port 8000   # 在 2_1 目录下
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.browser_runtime import (
    BrowserRuntimeError,
    get_browser_runtime_status,
    install_matching_chromedriver,
)
from core.controller import AppController
from services.agent_chat import AgentChatService, AgentConversationStore, build_agent_action_suggestions
from services.llm_provider import (
    LLMProviderError,
    build_provider_from_config,
    get_public_agent_config,
    save_agent_config,
    test_agent_provider_config,
)
from services.product_detail_collection import ProductDetailCollectionError

app = FastAPI(title="Amazon 选品助手 API", version="0.1.0")

# 轻量 Web 前端（D2）静态资源目录；与 API 同源托管，前端 fetch 无需跨域。
# 经 pkg_paths 定位：开发态 = 2_1/web（同旧行为），PyInstaller 冻结态 = 打包内资源目录。
from pkg_paths import resource_path  # noqa: E402

_WEB_DIR = resource_path("web")

# controller 是稳定的前端 API 层；service 变动不冲击本层。
_controller = AppController()
_agent_store = AgentConversationStore()


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "no-store"
        return response


def _ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": jsonable_encoder(data), "message": ""}


@app.exception_handler(BrowserRuntimeError)
async def _handle_browser_runtime(_request, exc: BrowserRuntimeError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "ok": False,
            "data": jsonable_encoder(exc.details),
            "message": str(exc),
            "code": exc.code,
        },
    )


@app.exception_handler(ProductDetailCollectionError)
async def _handle_product_detail_collection(_request, exc: ProductDetailCollectionError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"ok": False, "data": None, "message": str(exc), "code": "product_detail_unavailable"},
    )


@app.exception_handler(Exception)
async def _handle_all(_request, exc: Exception) -> JSONResponse:
    # 查询失败（如 MySQL 未启动、ASIN 不存在）兜底为结构化错误，前端可统一处理。
    return JSONResponse(status_code=500, content={"ok": False, "data": None, "message": str(exc)})


class KeywordWorkshopRunIn(BaseModel):
    seed_text: str | None = None
    seed_keywords: list[str] = Field(default_factory=list)
    marketplace: str = "US"
    use_suggest: bool = True
    use_titles: bool = True
    expand_suggest: bool = True
    max_suggest_queries_per_seed: int = 16
    max_title_rows: int = 300


class KeywordIdeaIdsIn(BaseModel):
    ids: list[int] = Field(default_factory=list)
    marketplace: str = "US"


class KeywordIdeaTrackingIn(KeywordIdeaIdsIn):
    target_snapshots: int = 3
    pages_per_keyword: int | None = None


class KeywordIdeaStatusIn(KeywordIdeaIdsIn):
    status: str


class KeywordRunStatusIn(BaseModel):
    status: str
    marketplace: str = "US"


class KeywordLibraryTrackingIn(BaseModel):
    ids: list[int] = Field(default_factory=list)
    marketplace: str = "US"
    target_snapshots: int = 3
    pages_per_keyword: int | None = None


class DesktopOpenWebIn(BaseModel):
    path: str = "/"


class DesktopOpenAmazonProductIn(BaseModel):
    asin: str
    marketplace: str = "US"
    product_url: str | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return _ok({"status": "ok"})


@app.post("/api/desktop/open-web")
def desktop_open_web(body: DesktopOpenWebIn, request: Request) -> dict[str, Any]:
    import webbrowser

    path = (body.path or "/").strip().replace("\\", "/")
    if "://" in path or path.startswith("//"):
        raise HTTPException(status_code=400, detail="只允许打开当前本地应用页面")
    if not path.startswith("/"):
        path = "/" + path

    base_url = str(request.base_url).rstrip("/")
    if not (base_url.startswith("http://127.0.0.1:") or base_url.startswith("http://localhost:")):
        raise HTTPException(status_code=400, detail="只允许打开本地应用页面")

    url = f"{base_url}{path}"
    webbrowser.open(url, new=2)
    return _ok({"url": url})


@app.post("/api/desktop/open-amazon-product")
def desktop_open_amazon_product(body: DesktopOpenAmazonProductIn) -> dict[str, Any]:
    import webbrowser

    from services.amazon_urls import amazon_product_url

    url = amazon_product_url(
        body.asin,
        marketplace=body.marketplace,
        source_url=body.product_url,
    )
    webbrowser.open(url, new=2)
    return _ok({"url": url})


@app.get("/api/recommendations")
def recommendations(
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "total_score",
    sort_dir: str = "desc",
    min_score: float | None = None,
) -> dict[str, Any]:
    return _ok(
        _controller.get_recommendations_page(
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            min_score=min_score,
        )
    )


@app.get("/api/products")
def products(
    limit: int = 100,
    offset: int = 0,
    keyword: str | None = None,
    keyword_exact: bool = False,
    min_score: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_reviews: int | None = None,
    sort_by: str = "total_score",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    return _ok(
        _controller.get_product_pool_page(
            limit=limit,
            offset=offset,
            keyword=keyword,
            keyword_exact=keyword_exact,
            min_score=min_score,
            min_price=min_price,
            max_price=max_price,
            max_reviews=max_reviews,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    )


@app.get("/api/products/{asin}")
def product_detail(asin: str) -> dict[str, Any]:
    return _ok(_controller.get_product_history(asin))


@app.post("/api/products/{asin}/collect-detail")
def product_detail_collect(asin: str) -> dict[str, Any]:
    # 单商品、单页面、用户主动触发；复用共享 Chrome，遇验证即停并保存拦截 HTML。
    return _ok(_controller.collect_product_detail(asin))


@app.get("/api/products/{asin}/image")
def product_image(asin: str, large: bool = False):
    # 商品主图联网缓存：首次查看时从采集存下的 Amazon image_url 下载并缓存到本地，
    # 之后命中缓存。仅允许 Amazon 媒体域名，纯只读派生数据。无图/失败回 404。
    # large=1 取原始全分辨率图（点击放大看细节），同样缓存。
    from services.product_image_cache import content_type_for, fetch_product_image

    path = fetch_product_image(asin, large=large)
    if path is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "data": None, "message": "无可用商品图"},
        )
    return FileResponse(
        path,
        media_type=content_type_for(path),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/products/{asin}/trend")
def product_trend(asin: str) -> dict[str, Any]:
    # 趋势置信度评估（Claude-Task 趋势模块）：服务端用 assess_product_trend 算，
    # 保持算法单一真源；前端只渲染。纯只读、不改评分口径、不接 score_record。
    from services.trend_analysis import assess_product_trend

    detail = _controller.get_product_history(asin)
    snapshots = detail.get("snapshots", []) if isinstance(detail, dict) else []
    return _ok(assess_product_trend(snapshots))


@app.get("/api/products/{asin}/advice")
def product_advice(asin: str) -> dict[str, Any]:
    # 选品建议（结论/风险/进入策略）：透出 controller.get_product_advice，逻辑下沉
    # 至 services.product_advice，与 GUI 共享；纯只读、不改评分口径。
    return _ok(_controller.get_product_advice(asin))


@app.get("/api/keywords/opportunities")
def keyword_opportunities(
    limit: int = 100,
    offset: int = 0,
    keyword: str | None = None,
    min_products: int | None = None,
    sort_by: str = "opportunity_score",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    return _ok(
        _controller.get_keyword_opportunities_page(
            limit=limit,
            offset=offset,
            keyword=keyword,
            min_products=min_products,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    )


# ---------- 关键词资产库：已入库关键词统一查看 ----------

@app.get("/api/keyword-library/keywords")
def keyword_library_keywords(
    limit: int = 100,
    offset: int = 0,
    marketplace: str = "US",
    keyword: str | None = None,
    snapshot_filter: str = "all",
    tracking_filter: str = "all",
    source_filter: str = "all",
    sort_by: str = "latest_snapshot_at",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    from services.keyword_library import fetch_keyword_assets_page

    return _ok(
        fetch_keyword_assets_page(
            limit=limit,
            offset=offset,
            marketplace=marketplace,
            keyword=keyword,
            snapshot_filter=snapshot_filter,
            tracking_filter=tracking_filter,
            source_filter=source_filter,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    )


@app.get("/api/keyword-library/tree")
def keyword_library_tree(
    marketplace: str = "US",
    keyword: str | None = None,
    snapshot_filter: str = "all",
    tracking_filter: str = "all",
    source_filter: str = "all",
    max_keywords: int = 500,
) -> dict[str, Any]:
    from services.keyword_library import fetch_keyword_asset_tree

    return _ok(
        fetch_keyword_asset_tree(
            marketplace=marketplace,
            keyword=keyword,
            snapshot_filter=snapshot_filter,
            tracking_filter=tracking_filter,
            source_filter=source_filter,
            max_keywords=max_keywords,
        )
    )


@app.get("/api/keyword-library/keywords/{keyword_id}")
def keyword_library_detail(keyword_id: int) -> dict[str, Any]:
    from services.keyword_library import fetch_keyword_asset_detail

    return _ok(fetch_keyword_asset_detail(keyword_id))


@app.post("/api/keyword-library/keywords/create-tracking")
def keyword_library_create_tracking(body: KeywordLibraryTrackingIn) -> dict[str, Any]:
    from services.keyword_library import create_tracking_for_keywords

    return _ok(
        create_tracking_for_keywords(
            body.ids,
            marketplace=body.marketplace,
            target_snapshots=body.target_snapshots,
            pages_per_keyword=body.pages_per_keyword,
        )
    )


# ---------- 关键词创意工坊：生成候选、人工推广、复用追踪任务 ----------

@app.post("/api/keyword-workshop/runs")
def keyword_workshop_run(body: KeywordWorkshopRunIn) -> dict[str, Any]:
    from services.keyword_workshop import run_keyword_workshop

    seeds: list[str] | str = body.seed_keywords or (body.seed_text or "")
    result = run_keyword_workshop(
        seed_keywords=seeds,
        marketplace=body.marketplace,
        use_suggest=body.use_suggest,
        use_titles=body.use_titles,
        expand_suggest=body.expand_suggest,
        max_suggest_queries_per_seed=body.max_suggest_queries_per_seed,
        max_title_rows=body.max_title_rows,
    )
    return _ok(result.to_dict())


@app.get("/api/keyword-workshop/runs")
def keyword_workshop_runs(limit: int = 20, offset: int = 0, marketplace: str = "US") -> dict[str, Any]:
    from services.keyword_workshop import fetch_keyword_idea_runs_page

    return _ok(fetch_keyword_idea_runs_page(limit=limit, offset=offset, marketplace=marketplace))


@app.get("/api/keyword-workshop/ideas")
def keyword_workshop_ideas(
    limit: int = 100,
    offset: int = 0,
    marketplace: str = "US",
    status: str | None = None,
    keyword: str | None = None,
    source: str | None = None,
    run_id: int | None = None,
    sort_by: str = "idea_score",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    from services.keyword_workshop import fetch_keyword_ideas_page

    return _ok(
        fetch_keyword_ideas_page(
            limit=limit,
            offset=offset,
            marketplace=marketplace,
            status=status,
            keyword=keyword,
            source=source,
            run_id=run_id,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    )


@app.post("/api/keyword-workshop/ideas/promote")
def keyword_workshop_promote(body: KeywordIdeaIdsIn) -> dict[str, Any]:
    from services.keyword_workshop import promote_keyword_ideas

    return _ok(promote_keyword_ideas(body.ids, marketplace=body.marketplace))


@app.post("/api/keyword-workshop/ideas/create-tracking")
def keyword_workshop_create_tracking(body: KeywordIdeaTrackingIn) -> dict[str, Any]:
    from services.keyword_workshop import create_tracking_from_keyword_ideas

    return _ok(
        create_tracking_from_keyword_ideas(
            body.ids,
            marketplace=body.marketplace,
            target_snapshots=body.target_snapshots,
            pages_per_keyword=body.pages_per_keyword,
        )
    )


@app.post("/api/keyword-workshop/ideas/status")
def keyword_workshop_set_status(body: KeywordIdeaStatusIn) -> dict[str, Any]:
    from services.keyword_workshop import update_keyword_idea_status

    return _ok(update_keyword_idea_status(body.ids, body.status, marketplace=body.marketplace))


@app.post("/api/keyword-workshop/runs/{run_id}/ideas/status")
def keyword_workshop_set_run_status(run_id: int, body: KeywordRunStatusIn) -> dict[str, Any]:
    from services.keyword_workshop import update_keyword_idea_status_by_run

    return _ok(update_keyword_idea_status_by_run(run_id, body.status, marketplace=body.marketplace))


@app.get("/api/reviews/insights")
def review_insights(limit: int = 100, keyword: str | None = None) -> dict[str, Any]:
    return _ok(_controller.get_review_insights(limit=limit, keyword=keyword))


@app.get("/api/tasks")
def tasks(limit: int = 100, status: str | None = None) -> dict[str, Any]:
    return _ok(_controller.get_task_jobs(limit=limit, status=status))


# ---------- 关键词追踪（C3 对接）：写 / 联网类端点 ----------
# 边界：建任务/改状态/删除是 DB 写（不碰采集口径）；/check 的 execute=True 是
# 联网采集（复用 B1 runner，照守采集边界），属危险操作——前端须二次确认后才传
# execute=true，默认 execute=false 只做 dry-run 预览、不联网。本层不绕过任何边界。

class TrackingTaskIn(BaseModel):
    keyword: str
    target_snapshots: int = 3
    marketplace: str = "US"
    pages_per_keyword: int | None = None


class TrackingStatusIn(BaseModel):
    status: str  # active / paused / completed / error


class TrackingCheckIn(BaseModel):
    execute: bool = False
    task_id: int | None = None


class CrawlRunIn(BaseModel):
    keyword: str
    pages: int | None = None


class BrowserDriverInstallIn(BaseModel):
    confirmed: bool = False


class CrawlQueueIn(BaseModel):
    name: str
    items: list[dict[str, Any]] = []


class AgentChatIn(BaseModel):
    conversation_id: str | None = None
    message: str | None = None
    confirm: dict[str, Any] | None = None
    client_context: dict[str, Any] | None = None


class AgentSuggestionsIn(BaseModel):
    client_context: dict[str, Any] | None = None


class AgentConfigIn(BaseModel):
    provider: str = "openai_compatible"
    base_url: str = ""
    api_key: str | None = None
    model: str = ""
    supports_tool_calls: bool = True
    temperature: float = 0.2
    max_tokens: int = 2400
    timeout_seconds: int = 60


@app.get("/api/tracking/tasks")
def tracking_list(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    from services.keyword_tracking import list_tracking_tasks

    return _ok(list_tracking_tasks(status=status, limit=limit))


@app.post("/api/tracking/tasks")
def tracking_create(body: TrackingTaskIn) -> dict[str, Any]:
    from services.keyword_tracking import create_tracking_task

    task = create_tracking_task(
        marketplace=body.marketplace,
        keyword=body.keyword,
        target_snapshots=body.target_snapshots,
        pages_per_keyword=body.pages_per_keyword,
    )
    return _ok(task)


@app.post("/api/tracking/tasks/{task_id}/status")
def tracking_set_status(task_id: int, body: TrackingStatusIn) -> dict[str, Any]:
    from services.keyword_tracking import update_tracking_task_status

    return _ok(update_tracking_task_status(task_id, body.status))


@app.delete("/api/tracking/tasks/{task_id}")
def tracking_delete(task_id: int) -> dict[str, Any]:
    from services.keyword_tracking import delete_tracking_task

    return _ok({"deleted": delete_tracking_task(task_id)})


@app.post("/api/tracking/check")
def tracking_check(body: TrackingCheckIn) -> dict[str, Any]:
    # execute=False 只预览到期情况、不联网（安全默认）；execute=True 串行执行真实采集
    # （联网，守采集边界），需前端确认后传入。
    # 真实采集复用共享 _controller 的持久浏览器会话（与手动采集/预开同一实例，复用已暖
    # 会话、采完不关）；dry-run 预览不碰浏览器，不传 controller。
    from services.keyword_tracking_scheduler import run_keyword_tracking_scheduler

    return _ok(
        run_keyword_tracking_scheduler(
            execute=body.execute,
            task_id=body.task_id,
            controller=_controller if body.execute else None,
        )
    )


# ---------- 手动运行爬取（GUI "运行爬取" 的 Web 入口） ----------
# 边界：仅按关键词打开 Amazon 搜索页并保存 HTML 到 html/<关键词>/；不入库、不评分。
# 联网操作由前端二次确认后触发；controller 内部做阻断/空页检测，遇异常即停。

@app.post("/api/crawl/run")
def crawl_run(body: CrawlRunIn) -> dict[str, Any]:
    return _ok(_controller.run_keyword_crawl(body.keyword, pages=body.pages, record_job=True))


@app.post("/api/crawl/run-import")
def crawl_run_import(body: CrawlRunIn) -> dict[str, Any]:
    # 采集队列单元：抓页存 HTML + 自动写 MySQL（不触发仓库同步，队尾统一同步）。
    # 联网 + 写库的危险操作，由前端队列在用户确认后逐词调用，照守采集边界。
    return _ok(_controller.run_keyword_crawl_and_import(body.keyword, pages=body.pages))


@app.get("/api/crawl/browser-runtime")
def crawl_browser_runtime() -> dict[str, Any]:
    """Read-only preflight; never downloads a browser or driver."""
    return _ok(get_browser_runtime_status())


@app.post("/api/crawl/browser-driver/install")
def crawl_browser_driver_install(body: BrowserDriverInstallIn) -> dict[str, Any]:
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="下载浏览器驱动前必须由用户明确确认")
    return _ok(install_matching_chromedriver())


@app.post("/api/crawl/open-amazon")
def crawl_open_amazon() -> dict[str, Any]:
    return _ok(_controller.open_amazon_page())


# 命名采集队列（工作流配置，本地 JSON，非业务数据、不进 MySQL）。
@app.get("/api/crawl/queues")
def crawl_queues_list() -> dict[str, Any]:
    from services.crawl_queues import list_queues

    return _ok(list_queues())


@app.post("/api/crawl/queues")
def crawl_queues_save(body: CrawlQueueIn) -> dict[str, Any]:
    from services.crawl_queues import save_queue

    return _ok(save_queue(body.name, body.items))


@app.delete("/api/crawl/queues/{name}")
def crawl_queues_delete(name: str) -> dict[str, Any]:
    from services.crawl_queues import delete_queue

    return _ok({"deleted": delete_queue(name)})


# ---------- 本地 HTML 入库（阶段1 单元①·透出现有 ingestion） ----------
# 边界（见 decisions/2026-06-20-本地选品分析工作台打包路线.md §四）：
# - 只允许 html/ 白名单目录下的 .html 文件；允许子目录相对路径（如 keyword/p1.html），
#   但必须来自后端递归白名单，拒绝绝对路径/上跳，杜绝"前端传任意服务器路径"。
# - 预览(只读解析)与入库(写库)分两步；入库前端须二次确认。透出既有 controller
#   方法，不新造业务逻辑、不改评分/采集口径。

_HTML_IMPORT_DIR = ROOT / "html"


class HtmlImportIn(BaseModel):
    files: list[str]
    keyword: str | None = None


def _list_html_files() -> list[str]:
    if not _HTML_IMPORT_DIR.is_dir():
        return []
    files: list[str] = []
    for path in _HTML_IMPORT_DIR.rglob("*.html"):
        rel = path.relative_to(_HTML_IMPORT_DIR)
        if rel.parts and rel.parts[0] in {"_blocked", "_details"}:
            continue
        files.append(rel.as_posix())
    return sorted(files)


def _safe_html_files(names: list[str]) -> list[str]:
    allowed = set(_list_html_files())
    safe: list[str] = []
    for name in names or []:
        base = (name or "").strip().replace("\\", "/")
        if (
            not base
            or base.startswith("/")
            or base.startswith("../")
            or "/../" in base
            or Path(base).is_absolute()
            or base not in allowed
        ):
            raise ValueError(f"非法或不存在的 HTML 文件名：{name!r}（仅允许 html/ 目录下文件）")
        safe.append(f"html/{base}")
    if not safe:
        raise ValueError("未选择任何 HTML 文件")
    return safe


@app.get("/api/import/html/files")
def import_html_files() -> dict[str, Any]:
    return _ok({"dir": "html", "files": _list_html_files()})


@app.post("/api/import/html/preview")
def import_html_preview(body: HtmlImportIn) -> dict[str, Any]:
    files = _safe_html_files(body.files)
    return _ok(_controller.preview_files_for_database(files, keyword=body.keyword))


@app.post("/api/import/html/commit")
def import_html_commit(body: HtmlImportIn) -> dict[str, Any]:
    # 写库：前端二次确认后调用，透出既有 import_files_to_database。
    files = _safe_html_files(body.files)
    return _ok(_controller.import_files_to_database(files, keyword=body.keyword))


# ---------- 分析仓库手动同步（阶段1 单元③·透出现有 sync_analytics_warehouse） ----------
# 边界：单向 MySQL→DuckDB/Parquet 分析副本，不反写主库（架构基线）。透出既有
# controller 方法，不改同步逻辑/聚合口径。前端二次确认后触发。

@app.post("/api/warehouse/sync")
def warehouse_sync() -> dict[str, Any]:
    return _ok(_controller.sync_analytics_warehouse())


# ---------- 用户设置（S3 设置页·透出 services.settings） ----------
# 边界：B 层（采集间隔/页数/72h/快照过期）由服务端按安全边界强制校验，不信前端；
# C 层自定义评分只作独立参考层、不替换标准评分口径。GET 返回设置+schema，POST 回报调整记录。

class SettingsPatchIn(BaseModel):
    patch: dict[str, Any]


@app.get("/api/settings")
def settings_get() -> dict[str, Any]:
    return _ok(_controller.get_settings())


@app.post("/api/settings")
def settings_update(body: SettingsPatchIn) -> dict[str, Any]:
    return _ok(_controller.update_settings(body.patch))


# ---------- 评论导入（阶段1 单元②·透出现有 review_import / review_html_export） ----------
# 边界同单元①：白名单只允许 reviews/ 目录下文件、只收 basename、拒绝路径分隔/上跳。
# CSV/JSON 导入分预览(只读)/入库(写库，前端二次确认)；HTML 解析仅离线本地、不联网、不写业务库。

_REVIEW_DIR = ROOT / "reviews"


class ReviewImportIn(BaseModel):
    file: str
    default_asin: str | None = None


class ReviewParseIn(BaseModel):
    files: list[str]
    output_format: str = "csv"
    default_asin: str | None = None


def _list_review_files() -> dict[str, list[str]]:
    if not _REVIEW_DIR.is_dir():
        return {"import_files": [], "html_files": []}
    items = [p for p in _REVIEW_DIR.iterdir() if p.is_file()]
    return {
        "import_files": sorted(p.name for p in items if p.suffix.lower() in (".csv", ".json")),
        "html_files": sorted(p.name for p in items if p.suffix.lower() in (".html", ".htm")),
    }


def _safe_review_path(name: str, allowed: set[str]) -> str:
    base = (name or "").strip()
    if not base or base != Path(base).name or base not in allowed:
        raise ValueError(f"非法或不存在的评论文件：{name!r}（仅允许 reviews/ 目录下文件）")
    return str(_REVIEW_DIR / base)


@app.get("/api/import/reviews/files")
def import_review_files() -> dict[str, Any]:
    return _ok({"dir": "reviews", **_list_review_files()})


@app.post("/api/import/reviews/preview")
def import_review_preview(body: ReviewImportIn) -> dict[str, Any]:
    path = _safe_review_path(body.file, set(_list_review_files()["import_files"]))
    return _ok(_controller.preview_review_import(path, default_asin=body.default_asin))


@app.post("/api/import/reviews/commit")
def import_review_commit(body: ReviewImportIn) -> dict[str, Any]:
    # 写库：前端二次确认后调用，透出既有 import_review_file（含去重 + 刷新洞察）。
    path = _safe_review_path(body.file, set(_list_review_files()["import_files"]))
    return _ok(_controller.import_review_file(path, default_asin=body.default_asin))


@app.post("/api/import/reviews/parse-html")
def import_review_parse_html(body: ReviewParseIn) -> dict[str, Any]:
    allowed = set(_list_review_files()["html_files"])
    paths = [_safe_review_path(n, allowed) for n in (body.files or [])]
    if not paths:
        raise ValueError("未选择任何评论 HTML 文件")
    fmt = body.output_format if body.output_format in ("csv", "json") else "csv"
    return _ok(_controller.export_review_html(paths, output_format=fmt, default_asin=body.default_asin))


# ---------- 内置 Agent（M1~M4）：模型配置 + tool 闭环 ----------

def _build_agent_provider():
    return build_provider_from_config()


@app.get("/api/agent/config")
def agent_config_get() -> dict[str, Any]:
    return _ok(get_public_agent_config())


@app.put("/api/agent/config")
def agent_config_save(body: AgentConfigIn) -> dict[str, Any]:
    try:
        return _ok(save_agent_config(body.dict()))
    except LLMProviderError as exc:
        return {"ok": False, "data": None, "message": str(exc)}


@app.post("/api/agent/config/test")
def agent_config_test(body: AgentConfigIn) -> dict[str, Any]:
    try:
        return _ok(test_agent_provider_config(body.dict()))
    except LLMProviderError as exc:
        return {"ok": False, "data": None, "message": str(exc)}


@app.post("/api/agent/chat")
def agent_chat(body: AgentChatIn) -> dict[str, Any]:
    try:
        provider = _build_agent_provider()
        service = AgentChatService(provider, controller=_controller, store=_agent_store)
        data = service.chat(
            conversation_id=body.conversation_id,
            message=body.message,
            confirm=body.confirm,
            client_context=body.client_context,
        )
    except LLMProviderError as exc:
        return {"ok": False, "data": None, "message": str(exc)}
    return _ok(data)


@app.post("/api/agent/suggestions")
def agent_suggestions(body: AgentSuggestionsIn) -> dict[str, Any]:
    return _ok({"action_suggestions": build_agent_action_suggestions(body.client_context)})


# 静态前端挂在最后：所有 /api/* 显式路由优先匹配，其余路径回落到 web/。
# html=True 让 "/" 返回 index.html，支持前端 hash 路由刷新。
if _WEB_DIR.is_dir():
    app.mount("/", NoCacheStaticFiles(directory=str(_WEB_DIR), html=True), name="web")
