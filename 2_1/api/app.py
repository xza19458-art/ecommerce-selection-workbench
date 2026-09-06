"""FastAPI API 层：把现有控制器与服务能力暴露为本地 REST 端点。

设计（见 decisions/2026-06-19-前端架构转Web.md §6）：
- 后端链路完全不变；本层只是新的"调用方"，把 controller 方法包成 HTTP。
- 查询、显式写入与联网端点共用统一响应契约；写入/联网动作仍由前端确认并遵守采集边界。
- 统一返回 `{"ok": bool, "data": ..., "message": str}`；异常兜底为 500 + message。

本地启动：
    uvicorn api.app:app --reload --port 8000   # 在 2_1 目录下
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pkg_paths import user_data_path

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
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
from services.metric_center import MetricInputError
from services.detail_reparse import DetailReparseError
from services.research_workspace import ResearchProjectError
from services.research_decision_report import ResearchDecisionReportError
from services.research_report_versions import ResearchReportVersionError
from services.research_review_queue import ResearchReviewQueueError
from services.research_monitoring import ResearchMonitoringError
from services.market_niches import MarketNicheError
from services.competitive_graph import CompetitiveGraphError
from services.scoring_v2 import ScoringV2Error
from services.scoring_calibration_export import ScoringCalibrationExportError
from services.domain_scoring import DomainScoringError
from api.contracts import ok as _ok
from api.routers import products as products_routes
from api.routers import domain_models as domain_model_routes
from api.routers import research_reports as research_report_routes
from api.routers import research_reviews as research_review_routes
from api.routers import research_monitoring as research_monitoring_routes
from api.routers.system import router as system_router
from api.routers.warehouse import router as warehouse_router
from repositories.products import ProductRepository

app = FastAPI(title="Amazon 选品助手 API", version="0.1.0")

# 轻量 Web 前端（D2）静态资源目录；与 API 同源托管，前端 fetch 无需跨域。
# 经 pkg_paths 定位：开发态 = 2_1/web（同旧行为），PyInstaller 冻结态 = 打包内资源目录。
from pkg_paths import resource_path  # noqa: E402

_WEB_DIR = resource_path("web")

# controller 是稳定的前端 API 层；service 变动不冲击本层。
_controller = AppController()
_agent_store = AgentConversationStore()
products_routes.configure_repository(
    ProductRepository(detail_collector=_controller.collect_product_detail)
)


class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "no-store"
        return response


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


@app.exception_handler(MetricInputError)
async def _handle_metric_input(_request, exc: MetricInputError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "metric_input_invalid"},
    )


@app.exception_handler(DetailReparseError)
async def _handle_detail_reparse(_request, exc: DetailReparseError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "detail_reparse_invalid"},
    )


@app.exception_handler(ResearchProjectError)
async def _handle_research_project(_request, exc: ResearchProjectError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "research_project_invalid"},
    )


@app.exception_handler(ResearchDecisionReportError)
async def _handle_research_decision_report(
    _request,
    exc: ResearchDecisionReportError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "research_report_invalid"},
    )


@app.exception_handler(ResearchReportVersionError)
async def _handle_research_report_version(
    _request,
    exc: ResearchReportVersionError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "data": jsonable_encoder(exc.details),
            "message": str(exc),
            "code": exc.code,
        },
    )


@app.exception_handler(ResearchReviewQueueError)
async def _handle_research_review_queue(
    _request,
    exc: ResearchReviewQueueError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "data": None,
            "message": str(exc),
            "code": "research_review_queue_invalid",
        },
    )


@app.exception_handler(ResearchMonitoringError)
async def _handle_research_monitoring(
    _request,
    exc: ResearchMonitoringError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "data": jsonable_encoder(exc.details),
            "message": str(exc),
            "code": exc.code,
        },
    )


@app.exception_handler(MarketNicheError)
async def _handle_market_niche(_request, exc: MarketNicheError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "market_niche_invalid"},
    )


@app.exception_handler(CompetitiveGraphError)
async def _handle_competitive_graph(_request, exc: CompetitiveGraphError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "competitive_graph_invalid"},
    )


@app.exception_handler(ScoringV2Error)
async def _handle_scoring_v2(_request, exc: ScoringV2Error) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "scoring_v2_invalid"},
    )


@app.exception_handler(ScoringCalibrationExportError)
async def _handle_scoring_calibration_export(_request, exc: ScoringCalibrationExportError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "scoring_calibration_export_invalid"},
    )


@app.exception_handler(DomainScoringError)
async def _handle_domain_scoring(_request, exc: DomainScoringError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "domain_scoring_invalid"},
    )


@app.exception_handler(RequestValidationError)
async def _handle_request_validation(_request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "data": {"errors": jsonable_encoder(exc.errors())},
            "message": "请求参数格式不正确。",
            "code": "request_validation_error",
        },
    )


@app.exception_handler(ValueError)
async def _handle_value_error(_request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"ok": False, "data": None, "message": str(exc), "code": "invalid_request"},
    )


@app.exception_handler(Exception)
async def _handle_all(_request, exc: Exception) -> JSONResponse:
    # 查询失败（如 MySQL 未启动、ASIN 不存在）兜底为结构化错误，前端可统一处理。
    return JSONResponse(status_code=500, content={"ok": False, "data": None, "message": str(exc)})


app.include_router(system_router)
app.include_router(warehouse_router)
app.include_router(products_routes.router)
app.include_router(domain_model_routes.router)
app.include_router(research_report_routes.router)
app.include_router(research_review_routes.router)
app.include_router(research_monitoring_routes.router)


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


class ProductMetricInputIn(BaseModel):
    period_start: str
    period_end: str
    source_type: str = "manual"
    source_label: str | None = None
    sessions: int | None = None
    page_views: int | None = None
    units_ordered: int | None = None
    orders: int | None = None
    ordered_sales: float | None = None
    featured_offer_percentage: float | str | None = None
    impressions: int | None = None
    clicks: int | None = None
    cart_adds: int | None = None
    purchases: int | None = None
    ad_spend: float | None = None
    ad_clicks: int | None = None
    ad_orders: int | None = None
    ad_sales: float | None = None
    total_sales: float | None = None
    unit_purchase_cost: float | None = None
    unit_shipping_cost: float | None = None
    unit_fba_fee: float | None = None
    unit_referral_fee: float | None = None
    unit_other_cost: float | None = None
    assumed_cvr_low: float | str | None = None
    assumed_cvr_base: float | str | None = None
    assumed_cvr_high: float | str | None = None
    notes: str | None = None


class MetricInputImportIn(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)


class DetailReparseIn(BaseModel):
    paths: list[str] = Field(default_factory=list)


class ResearchProjectCreateIn(BaseModel):
    name: str
    marketplace: str = "US"
    objective: str | None = None
    strategy: str | None = None


class ResearchProjectUpdateIn(BaseModel):
    name: str | None = None
    objective: str | None = None
    strategy: str | None = None


class ResearchProjectStatusIn(BaseModel):
    status: str
    decision_summary: str | None = None
    confirmed: bool = False
    evaluated_on: str | None = None
    expected_report_fingerprint: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=64)
    version_note: str | None = Field(default=None, max_length=2000)


class ResearchProjectProductsIn(BaseModel):
    asins: list[str] = Field(default_factory=list)
    role: str = "candidate"
    notes: str | None = None


class ResearchProjectKeywordsIn(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    role: str = "candidate"
    notes: str | None = None


class ResearchProjectNoteIn(BaseModel):
    note_type: str = "observation"
    content: str


class MarketNicheCreateIn(BaseModel):
    name: str
    marketplace: str = "US"
    definition: str | None = None
    category_scope: str | None = None


class MarketNicheUpdateIn(BaseModel):
    name: str | None = None
    status: str | None = None
    definition: str | None = None
    category_scope: str | None = None


class MarketNicheKeywordsIn(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    role: str = "core"
    notes: str | None = None


class MarketNicheProductsIn(BaseModel):
    asins: list[str] = Field(default_factory=list)
    role: str = "benchmark"
    notes: str | None = None


class MarketNicheProjectsIn(BaseModel):
    project_ids: list[int] = Field(default_factory=list)
    role: str = "candidate"


class ScoringCalibrationExportIn(BaseModel):
    payload: dict[str, Any]


@app.get("/api/recommendations")
def recommendations(
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "total_score",
    sort_dir: str = "desc",
    keyword: str | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    min_rating: float | None = None,
    max_rating: float | None = None,
    min_reviews: int | None = None,
    max_reviews: int | None = None,
    min_bought: int | None = None,
    max_bought: int | None = None,
    min_rank: int | None = None,
    max_rank: int | None = None,
    deal_status: Literal["all", "deal", "regular"] = "all",
    size_status: Literal["all", "known", "missing"] = "all",
) -> dict[str, Any]:
    return _ok(
        _controller.get_recommendations_page(
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
            keyword=keyword,
            min_score=min_score,
            max_score=max_score,
            min_price=min_price,
            max_price=max_price,
            min_rating=min_rating,
            max_rating=max_rating,
            min_reviews=min_reviews,
            max_reviews=max_reviews,
            min_bought=min_bought,
            max_bought=max_bought,
            min_rank=min_rank,
            max_rank=max_rank,
            deal_status=deal_status,
            size_status=size_status,
        )
    )


@app.get("/api/scoring-v2/replay")
def scoring_v2_replay(
    limit: int = 50,
    offset: int = 0,
    marketplace: str = "US",
    keyword: str | None = None,
    strategy: str = "balanced",
    recommendation: str | None = None,
    min_confidence: float | None = None,
    max_risk: float | None = None,
    sort_by: str = "opportunity_score",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    """Read-only P7 shadow replay; never updates the production score table."""
    from services.scoring_v2 import fetch_scoring_v2_replay

    return _ok(
        fetch_scoring_v2_replay(
            limit=limit,
            offset=offset,
            marketplace=marketplace,
            keyword=keyword,
            strategy=strategy,
            recommendation=recommendation,
            min_confidence=min_confidence,
            max_risk=max_risk,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    )


@app.get("/api/scoring-v2/calibration")
def scoring_v2_calibration(
    sample_per_bucket: int = 2,
    marketplace: str = "US",
    keyword: str | None = None,
    strategy: str = "balanced",
    sample_seed: str = "baseline",
    exclude_asin: list[str] | None = Query(default=None),
) -> dict[str, Any]:
    """Read-only P7.1 sample; drafts stay in the browser unless explicitly exported."""
    from services.scoring_v2 import fetch_scoring_v2_calibration

    return _ok(
        fetch_scoring_v2_calibration(
            sample_per_bucket=sample_per_bucket,
            marketplace=marketplace,
            keyword=keyword,
            strategy=strategy,
            sample_seed=sample_seed,
            exclude_asins=exclude_asin or (),
        )
    )


@app.post("/api/scoring-v2/calibration/export")
def scoring_v2_calibration_export(body: ScoringCalibrationExportIn) -> dict[str, Any]:
    """Persist an explicit calibration review export in the fixed local export directory."""
    from services.scoring_calibration_export import export_scoring_calibration

    return _ok(export_scoring_calibration(body.payload))


@app.get("/api/scoring-v2/calibration/reviews")
def scoring_v2_calibration_reviews(limit: int = 200) -> dict[str, Any]:
    """Read durable human reviews from local calibration exports; never writes MySQL."""
    from services.scoring_calibration_export import load_exported_scoring_calibration_reviews

    return _ok(load_exported_scoring_calibration_reviews(limit=limit))


@app.get("/api/scoring-v2/calibration/export")
def scoring_v2_calibration_export_capability() -> dict[str, Any]:
    """Expose the fixed local destination without writing a file."""
    from services.scoring_calibration_export import EXPORT_SCHEMA_VERSION, scoring_calibration_export_directory

    return _ok(
        {
            "available": True,
            "directory": str(scoring_calibration_export_directory()),
            "schema_version": EXPORT_SCHEMA_VERSION,
            "default_scope": "reviewed",
            "writes_database": False,
        }
    )


@app.get("/api/metrics/products")
def metric_products(
    limit: int = 50,
    offset: int = 0,
    keyword: str | None = None,
) -> dict[str, Any]:
    from services.metric_center import fetch_metric_products_page

    return _ok(fetch_metric_products_page(limit=limit, offset=offset, keyword=keyword))


@app.get("/api/metrics/products/{asin}")
def metric_product_detail(asin: str) -> dict[str, Any]:
    from services.metric_center import get_product_metric_center

    return _ok(get_product_metric_center(asin))


@app.post("/api/metrics/products/{asin}/inputs")
def metric_product_input(asin: str, body: ProductMetricInputIn) -> dict[str, Any]:
    from services.metric_center import save_metric_input

    return _ok(save_metric_input(asin, body.dict()))


@app.post("/api/metrics/inputs/import")
def metric_inputs_import(body: MetricInputImportIn) -> dict[str, Any]:
    from services.metric_center import import_metric_inputs

    return _ok(import_metric_inputs(body.rows))


@app.post("/api/metrics/products/{asin}/refresh-estimates")
def metric_product_estimates_refresh(asin: str) -> dict[str, Any]:
    from services.metric_center import refresh_product_estimates

    return _ok(refresh_product_estimates(asin))


@app.get("/api/metrics/evidence")
def metric_evidence_queue(
    limit: int = 50,
    offset: int = 0,
    file_limit: int = 100,
    stale_days: int = 30,
    refresh_cache: bool = False,
    priority: Literal["all", "focus", "project", "planned", "recent", "routine"] = "all",
    project_id: int | None = None,
) -> dict[str, Any]:
    from services.detail_reparse import list_detail_reparse_candidates

    query: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
        "file_limit": file_limit,
        "stale_days": stale_days,
        "force_refresh": refresh_cache,
        "priority": priority,
    }
    if project_id is not None:
        query["project_id"] = project_id
    return _ok(list_detail_reparse_candidates(**query))


@app.post("/api/metrics/evidence/preview")
def metric_evidence_preview(body: DetailReparseIn) -> dict[str, Any]:
    from services.detail_reparse import preview_detail_files

    return _ok(preview_detail_files(body.paths))


@app.post("/api/metrics/evidence/apply")
def metric_evidence_apply(body: DetailReparseIn) -> dict[str, Any]:
    # 纯本地回放；前端确认后调用。服务端会重新读取并校验白名单文件。
    from services.detail_reparse import apply_detail_files

    return _ok(apply_detail_files(body.paths))


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


# ---------- 研究项目工作区：连接已有商品、关键词、证据与人工结论 ----------

@app.get("/api/research-projects")
def research_projects(
    limit: int = 50,
    offset: int = 0,
    marketplace: str = "US",
    status: str | None = None,
    keyword: str | None = None,
    sort_by: str = "updated_at",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    from services.research_workspace import fetch_research_projects_page

    return _ok(
        fetch_research_projects_page(
            limit=limit,
            offset=offset,
            marketplace=marketplace,
            status=status,
            keyword=keyword,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    )


@app.post("/api/research-projects")
def research_project_create(body: ResearchProjectCreateIn) -> dict[str, Any]:
    from services.research_workspace import create_research_project

    return _ok(
        create_research_project(
            body.name,
            marketplace=body.marketplace,
            objective=body.objective,
            strategy=body.strategy,
        )
    )


@app.get("/api/research-projects/{project_id}")
def research_project_detail(project_id: int) -> dict[str, Any]:
    from services.research_workspace import get_research_project

    return _ok(get_research_project(project_id))


@app.patch("/api/research-projects/{project_id}")
def research_project_update(project_id: int, body: ResearchProjectUpdateIn) -> dict[str, Any]:
    from services.research_workspace import update_research_project

    return _ok(
        update_research_project(
            project_id,
            name=body.name,
            objective=body.objective,
            strategy=body.strategy,
        )
    )


@app.post("/api/research-projects/{project_id}/status")
def research_project_status(project_id: int, body: ResearchProjectStatusIn) -> dict[str, Any]:
    from services.research_workspace import set_research_project_status

    return _ok(
        set_research_project_status(
            project_id,
            body.status,
            decision_summary=body.decision_summary,
            confirmed=body.confirmed,
            evaluated_on=body.evaluated_on,
            expected_report_fingerprint=body.expected_report_fingerprint,
            idempotency_key=body.idempotency_key,
            version_note=body.version_note,
        )
    )


@app.post("/api/research-projects/{project_id}/products")
def research_project_products_add(project_id: int, body: ResearchProjectProductsIn) -> dict[str, Any]:
    from services.research_workspace import add_research_project_products

    return _ok(
        add_research_project_products(
            project_id,
            body.asins,
            role=body.role,
            notes=body.notes,
        )
    )


@app.delete("/api/research-projects/{project_id}/products/{product_id}")
def research_project_product_delete(project_id: int, product_id: int) -> dict[str, Any]:
    from services.research_workspace import remove_research_project_product

    return _ok(remove_research_project_product(project_id, product_id))


@app.post("/api/research-projects/{project_id}/keywords")
def research_project_keywords_add(project_id: int, body: ResearchProjectKeywordsIn) -> dict[str, Any]:
    from services.research_workspace import add_research_project_keywords

    return _ok(
        add_research_project_keywords(
            project_id,
            body.keywords,
            role=body.role,
            notes=body.notes,
        )
    )


@app.delete("/api/research-projects/{project_id}/keywords/{keyword_id}")
def research_project_keyword_delete(project_id: int, keyword_id: int) -> dict[str, Any]:
    from services.research_workspace import remove_research_project_keyword

    return _ok(remove_research_project_keyword(project_id, keyword_id))


@app.post("/api/research-projects/{project_id}/notes")
def research_project_note_add(project_id: int, body: ResearchProjectNoteIn) -> dict[str, Any]:
    from services.research_workspace import add_research_project_note

    return _ok(add_research_project_note(project_id, body.note_type, body.content))


@app.delete("/api/research-projects/{project_id}/notes/{note_id}")
def research_project_note_delete(project_id: int, note_id: int) -> dict[str, Any]:
    from services.research_workspace import delete_research_project_note

    return _ok(delete_research_project_note(project_id, note_id))


# ---------- 市场与利基：显式成员、现有证据聚合与人工项目关系 ----------

@app.get("/api/market-niches")
def market_niches(
    limit: int = 50,
    offset: int = 0,
    marketplace: str = "US",
    status: str | None = None,
    keyword: str | None = None,
    sort_by: str = "updated_at",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    from services.market_niches import fetch_market_niches_page

    return _ok(
        fetch_market_niches_page(
            limit=limit,
            offset=offset,
            marketplace=marketplace,
            status=status,
            keyword=keyword,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    )


@app.post("/api/market-niches")
def market_niche_create(body: MarketNicheCreateIn) -> dict[str, Any]:
    from services.market_niches import create_market_niche

    return _ok(
        create_market_niche(
            body.name,
            marketplace=body.marketplace,
            definition=body.definition,
            category_scope=body.category_scope,
        )
    )


@app.get("/api/market-niches/{niche_id}")
def market_niche_detail(niche_id: int) -> dict[str, Any]:
    from services.market_niches import get_market_niche

    return _ok(get_market_niche(niche_id))


@app.patch("/api/market-niches/{niche_id}")
def market_niche_update(niche_id: int, body: MarketNicheUpdateIn) -> dict[str, Any]:
    from services.market_niches import update_market_niche

    return _ok(
        update_market_niche(
            niche_id,
            name=body.name,
            status=body.status,
            definition=body.definition,
            category_scope=body.category_scope,
        )
    )


@app.post("/api/market-niches/{niche_id}/keywords")
def market_niche_keywords_add(niche_id: int, body: MarketNicheKeywordsIn) -> dict[str, Any]:
    from services.market_niches import add_niche_keywords

    return _ok(add_niche_keywords(niche_id, body.keywords, role=body.role, notes=body.notes))


@app.delete("/api/market-niches/{niche_id}/keywords/{keyword_id}")
def market_niche_keyword_delete(niche_id: int, keyword_id: int) -> dict[str, Any]:
    from services.market_niches import remove_niche_keyword

    return _ok(remove_niche_keyword(niche_id, keyword_id))


@app.post("/api/market-niches/{niche_id}/products")
def market_niche_products_add(niche_id: int, body: MarketNicheProductsIn) -> dict[str, Any]:
    from services.market_niches import add_niche_products

    return _ok(add_niche_products(niche_id, body.asins, role=body.role, notes=body.notes))


@app.delete("/api/market-niches/{niche_id}/products/{product_id}")
def market_niche_product_delete(niche_id: int, product_id: int) -> dict[str, Any]:
    from services.market_niches import remove_niche_product

    return _ok(remove_niche_product(niche_id, product_id))


@app.post("/api/market-niches/{niche_id}/projects")
def market_niche_projects_add(niche_id: int, body: MarketNicheProjectsIn) -> dict[str, Any]:
    from services.market_niches import add_niche_projects

    return _ok(add_niche_projects(niche_id, body.project_ids, role=body.role))


@app.delete("/api/market-niches/{niche_id}/projects/{project_id}")
def market_niche_project_delete(niche_id: int, project_id: int) -> dict[str, Any]:
    from services.market_niches import remove_niche_project

    return _ok(remove_niche_project(niche_id, project_id))


@app.post("/api/market-niches/{niche_id}/snapshots")
def market_niche_snapshot_generate(niche_id: int) -> dict[str, Any]:
    from services.market_niches import generate_niche_snapshot

    return _ok(generate_niche_snapshot(niche_id))


# ---------- 竞品图谱：仅从利基快照冻结来源构建只读关系 ----------

@app.get("/api/competitive-graph/{niche_id}")
def competitive_graph(
    niche_id: int,
    snapshot_id: int | None = None,
    limit: int = 100,
    min_shared: int = 1,
    focus_asin: str | None = None,
) -> dict[str, Any]:
    from services.competitive_graph import get_competitive_graph

    return _ok(
        get_competitive_graph(
            niche_id,
            snapshot_id=snapshot_id,
            limit=limit,
            min_shared=min_shared,
            focus_asin=focus_asin,
        )
    )


@app.get("/api/reviews/insights")
def review_insights(limit: int = 100, keyword: str | None = None) -> dict[str, Any]:
    return _ok(_controller.get_review_insights(limit=limit, keyword=keyword))


@app.get("/api/tasks")
def tasks(limit: int = 100, status: str | None = None) -> dict[str, Any]:
    return _ok(_controller.get_task_jobs(limit=limit, status=status))


@app.get("/api/tasks/page")
def task_page(
    limit: int = 25,
    offset: int = 0,
    keyword: str | None = None,
    status: str | None = None,
    job_type: str | None = None,
) -> dict[str, Any]:
    return _ok(
        _controller.get_task_jobs_page(
            limit=limit,
            offset=offset,
            keyword=keyword,
            status=status,
            job_type=job_type,
        )
    )


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


@app.get("/api/tracking/tasks/{task_id}/evidence")
def tracking_evidence(task_id: int) -> dict[str, Any]:
    from services.tracking_evidence import build_tracking_task_evidence

    return _ok(build_tracking_task_evidence(task_id))


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

_HTML_IMPORT_DIR = user_data_path("html")


class HtmlImportIn(BaseModel):
    files: list[str]
    keyword: str | None = None
    confirmation_token: str | None = None
    expected_valid: int | None = Field(default=None, ge=0)
    confirmed: bool = False


def _list_html_files() -> list[str]:
    if not _HTML_IMPORT_DIR.is_dir():
        return []
    files: list[str] = []
    for path in _HTML_IMPORT_DIR.rglob("*.html"):
        rel = path.relative_to(_HTML_IMPORT_DIR)
        if any(part in {"_blocked", "_details"} for part in rel.parts):
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
    if not body.confirmed:
        raise ValueError("写入数据库前必须由用户明确确认。")
    if not body.confirmation_token or body.expected_valid is None:
        raise ValueError("请先完成本批 HTML 预览，再确认写入数据库。")
    files = _safe_html_files(body.files)
    return _ok(
        _controller.import_previewed_files_to_database(
            files,
            keyword=body.keyword,
            confirmation_token=body.confirmation_token,
            expected_valid=body.expected_valid,
        )
    )


# ---------- 评论导入（阶段1 单元②·透出现有 review_import / review_html_export） ----------
# 边界同单元①：白名单只允许 reviews/ 目录下文件、只收 basename、拒绝路径分隔/上跳。
# CSV/JSON 导入分预览(只读)/入库(写库，前端二次确认)；HTML 解析仅离线本地、不联网、不写业务库。

_REVIEW_DIR = user_data_path("reviews")


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
        return _ok(save_agent_config(body.model_dump()))
    except LLMProviderError as exc:
        return {"ok": False, "data": None, "message": str(exc)}


@app.post("/api/agent/config/test")
def agent_config_test(body: AgentConfigIn) -> dict[str, Any]:
    try:
        return _ok(test_agent_provider_config(body.model_dump()))
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
