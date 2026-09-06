"""Tool schema and dispatch for the in-app Agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from typing import Any, Callable

from core.controller import AppController


@dataclass(frozen=True)
class AgentToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    requires_confirmation: bool = False

    def to_provider_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "requires_confirmation": self.requires_confirmation,
        }


@dataclass(frozen=True)
class AgentToolResult:
    name: str
    input: dict[str, Any]
    ok: bool
    data: Any = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input": self.input,
            "ok": self.ok,
            "data": _json_safe(self.data),
            "message": self.message,
        }


class AgentToolExecutor:
    """Thin dispatcher over existing controller/service methods."""

    def __init__(self, controller: AppController | None = None) -> None:
        self.controller = controller or AppController()
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "query_app_overview": self._query_app_overview,
            "query_recommendations": self._query_recommendations,
            "query_products": self._query_products,
            "query_product_detail": self._query_product_detail,
            "query_product_metrics": self._query_product_metrics,
            "query_detail_evidence_priorities": self._query_detail_evidence_priorities,
            "query_product_trend": self._query_product_trend,
            "query_keyword_opportunities": self._query_keyword_opportunities,
            "query_keyword_groups": self._query_keyword_groups,
            "query_keyword_ideas": self._query_keyword_ideas,
            "query_research_projects": self._query_research_projects,
            "query_research_review_queue": self._query_research_review_queue,
            "query_research_decision_report": self._query_research_decision_report,
            "query_market_niches": self._query_market_niches,
            "query_competitive_graph": self._query_competitive_graph,
            "query_scoring_v2_replay": self._query_scoring_v2_replay,
            "query_scoring_v2_calibration": self._query_scoring_v2_calibration,
            "query_review_insights": self._query_review_insights,
            "query_tracking_tasks": self._query_tracking_tasks,
            "query_tracking_evidence": self._query_tracking_evidence,
            "query_tasks": self._query_tasks,
            "open_amazon_page": self._open_amazon_page,
            "create_keyword_tracking": self._create_keyword_tracking,
            "set_keyword_tracking_status": self._set_keyword_tracking_status,
            "trigger_collection": self._trigger_collection,
        }

    def execute(self, name: str, tool_input: dict[str, Any] | None) -> AgentToolResult:
        normalized_input = dict(tool_input or {})
        handler = self._handlers.get(name)
        if handler is None:
            return AgentToolResult(
                name=name,
                input=normalized_input,
                ok=False,
                message=f"未知工具：{name}",
            )
        try:
            return AgentToolResult(
                name=name,
                input=normalized_input,
                ok=True,
                data=handler(normalized_input),
            )
        except Exception as exc:
            return AgentToolResult(name=name, input=normalized_input, ok=False, message=str(exc))

    def _query_recommendations(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return self.controller.get_top_recommendations(limit=_int(data.get("limit"), 50, 1, 200))

    def _query_app_overview(self, data: dict[str, Any]) -> dict[str, Any]:
        limit = _int(data.get("limit"), 5, 1, 20)
        keyword = _optional_str(data.get("keyword"))
        keyword_section = _safe_call(lambda: self.controller.get_keyword_opportunities(limit=limit * 3, keyword=keyword))
        keyword_rows = keyword_section.get("data") if keyword_section.get("ok") else []
        overview = {
            "推荐榜": _safe_call(lambda: self.controller.get_top_recommendations(limit=limit)),
            "关键词机会": {
                **keyword_section,
                "data": (keyword_rows or [])[:limit] if keyword_section.get("ok") else None,
            },
            "关键词一级分组": _safe_call(
                lambda: _build_keyword_groups(
                    keyword_rows or [],
                    mode="tail",
                    max_groups=limit,
                    keywords_per_group=5,
                )
            ),
            "关键词创意候选": _safe_call(lambda: self._query_keyword_ideas({"limit": limit, "status": "candidate"})),
            "研究项目": _safe_call(lambda: self._query_research_projects({"limit": limit})),
            "研究复核队列": _safe_call(lambda: self._query_research_review_queue({"limit": limit})),
            "市场利基": _safe_call(lambda: self._query_market_niches({"limit": limit})),
            "评论洞察": _safe_call(lambda: self.controller.get_review_insights(limit=limit, keyword=keyword)),
            "追踪任务": _safe_call(lambda: self._tracking_tasks(limit=limit)),
            "任务中心": _safe_call(lambda: self.controller.get_task_jobs(limit=limit)),
        }
        overview["使用建议"] = [
            "回答全局问题时，先看推荐榜和关键词机会，再结合任务中心判断数据是否新鲜。",
            "如果评论洞察为空或证据很少，需要说明评论数据不足，不能编造低分痛点。",
            "如果商品历史快照少于 2 条，不能下趋势结论；少于 3 条时趋势置信度仍偏低。",
            "触发采集前建议先预开启 Amazon 页面并让用户处理地址、登录或验证码。",
        ]
        return overview

    def _query_products(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        keyword_scope = _optional_str(data.get("keyword_scope"))
        return self.controller.get_product_pool(
            limit=_int(data.get("limit"), 100, 1, 500),
            keyword=_optional_str(data.get("keyword")),
            keyword_exact=_bool(data.get("keyword_exact"), False),
            keyword_scope="observed" if keyword_scope == "observed" else "current",
            min_score=_optional_float(data.get("min_score")),
            max_score=_optional_float(data.get("max_score")),
            min_price=_optional_float(data.get("min_price")),
            max_price=_optional_float(data.get("max_price")),
            min_rating=_optional_float(data.get("min_rating")),
            max_rating=_optional_float(data.get("max_rating")),
            min_reviews=_optional_int(data.get("min_reviews")),
            max_reviews=_optional_int(data.get("max_reviews")),
            min_bought=_optional_int(data.get("min_bought")),
            max_bought=_optional_int(data.get("max_bought")),
            min_rank=_optional_int(data.get("min_rank")),
            max_rank=_optional_int(data.get("max_rank")),
            deal_status=_enum(data.get("deal_status"), {"all", "deal", "regular"}, "all"),
            size_status=_enum(data.get("size_status"), {"all", "known", "missing"}, "all"),
        )

    def _query_product_detail(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.controller.get_product_history(_required_str(data, "asin"))

    def _query_product_metrics(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.metric_center import get_product_metric_center

        return get_product_metric_center(_required_str(data, "asin"))

    def _query_detail_evidence_priorities(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.detail_reparse import MAX_SCAN_FILES, list_detail_reparse_candidates

        query = {
            "limit": _int(data.get("limit"), 10, 1, 50),
            "file_limit": MAX_SCAN_FILES,
            "stale_days": _int(data.get("stale_days"), 30, 1, 3650),
            "priority": _enum(
                data.get("priority"),
                {"all", "focus", "project", "planned", "recent", "routine"},
                "all",
            ),
        }
        project_id = _optional_int(data.get("project_id"))
        if project_id is not None:
            query["project_id"] = project_id
        result = list_detail_reparse_candidates(
            **query,
        )
        gaps = result.get("gaps") or {}
        rows = []
        for raw in gaps.get("rows") or []:
            row = dict(raw)
            action = dict(row.get("recommended_action") or {})
            rows.append(
                {
                    "asin": row.get("asin"),
                    "marketplace": row.get("marketplace"),
                    "title": row.get("title_zh") or row.get("title"),
                    "evidence_status": row.get("evidence_status"),
                    "evidence_gaps": list(row.get("reasons") or []),
                    "detail_collected_at": row.get("detail_collected_at"),
                    "last_seen_at": row.get("last_seen_at"),
                    "priority_tier": row.get("priority_tier"),
                    "priority_label": row.get("priority_label"),
                    "priority_reasons": list(row.get("priority_reasons") or []),
                    "recommended_action": {
                        "code": action.get("code"),
                        "label": action.get("label"),
                        "kind": action.get("kind"),
                        "reason": action.get("reason"),
                        "follow_up": action.get("follow_up"),
                        "requires_amazon": bool(action.get("accesses_amazon")),
                        "requires_explicit_user_action": bool(
                            action.get("user_confirmation_required")
                        ),
                        "automatic": False,
                        "local_evidence_checked": bool(action.get("local_evidence_checked")),
                        "local_evidence_scan_complete": bool(
                            action.get("local_evidence_scan_complete")
                        ),
                    },
                    "research_projects": [
                        {
                            "project_id": context.get("project_id"),
                            "project_name": context.get("project_name"),
                            "project_status_label": context.get("project_status_label"),
                            "role_label": context.get("role_label"),
                            "plan_active": context.get("plan_active"),
                            "plan_due": context.get("plan_due"),
                            "next_review_on": context.get("next_review_on"),
                        }
                        for context in row.get("research_context") or []
                    ],
                    "monitoring": row.get("monitoring"),
                }
            )
        policy = dict(gaps.get("priority_policy") or {})
        policy.update(
            {
                "read_only": True,
                "writes_database": False,
                "automatic_collection": False,
                "automatic_task_creation": False,
            }
        )
        return {
            "generated_at": result.get("generated_at"),
            "stale_days": result.get("stale_days"),
            "summary": result.get("summary"),
            "priority_filter": gaps.get("priority_filter"),
            "priority_filter_label": gaps.get("priority_filter_label"),
            "project_filter": gaps.get("project_filter"),
            "priority_summary": gaps.get("priority_summary"),
            "disposition_summary": gaps.get("disposition_summary"),
            "disposition_policy": gaps.get("disposition_policy"),
            "rows": rows,
            "policy": policy,
        }

    def _query_product_trend(self, data: dict[str, Any]) -> Any:
        from services.trend_analysis import assess_product_trend

        detail = self.controller.get_product_history(_required_str(data, "asin"))
        snapshots = detail.get("snapshots", []) if isinstance(detail, dict) else []
        return assess_product_trend(snapshots)

    def _query_keyword_opportunities(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return self.controller.get_keyword_opportunities(
            limit=_int(data.get("limit"), 100, 1, 500),
            keyword=_optional_str(data.get("keyword")),
            min_products=_optional_int(data.get("min_products")),
        )

    def _query_keyword_groups(self, data: dict[str, Any]) -> dict[str, Any]:
        rows = self.controller.get_keyword_opportunities(
            limit=_int(data.get("limit"), 500, 1, 500),
            keyword=_optional_str(data.get("keyword")),
            min_products=_optional_int(data.get("min_products")),
        )
        mode = _optional_str(data.get("mode")) or "tail"
        groups = _build_keyword_groups(
            rows,
            mode=mode,
            max_groups=_int(data.get("max_groups"), 20, 1, 100),
            keywords_per_group=_int(data.get("keywords_per_group"), 8, 1, 30),
        )
        return {
            "mode": mode if mode in {"tail", "first", "shared"} else "tail",
            "total_keywords": len(rows),
            "groups": groups,
        }

    def _query_keyword_ideas(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        from services.keyword_workshop import fetch_keyword_ideas_page

        page = fetch_keyword_ideas_page(
            limit=_int(data.get("limit"), 100, 1, 500),
            offset=0,
            marketplace=_optional_str(data.get("marketplace")) or "US",
            status=_optional_str(data.get("status")),
            keyword=_optional_str(data.get("keyword")),
            source=_optional_str(data.get("source")),
        )
        return page["rows"]

    def _query_research_projects(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        from services.research_workspace import fetch_research_projects_page

        page = fetch_research_projects_page(
            limit=_int(data.get("limit"), 50, 1, 200),
            offset=0,
            marketplace=_optional_str(data.get("marketplace")) or "US",
            status=_optional_str(data.get("status")),
            keyword=_optional_str(data.get("keyword")),
            sort_by="updated_at",
            sort_dir="desc",
        )
        return page["rows"]

    def _query_research_review_queue(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.research_review_queue import (
            compact_research_review_queue,
            fetch_research_review_queue,
        )

        page = fetch_research_review_queue(
            limit=_int(data.get("limit"), 25, 1, 100),
            offset=0,
            marketplace=_optional_str(data.get("marketplace")) or "US",
            status=_optional_str(data.get("status")),
            keyword=_optional_str(data.get("keyword")),
            attention=_optional_str(data.get("attention")),
            monitoring=_optional_str(data.get("monitoring")),
            as_of=_optional_str(data.get("as_of")),
        )
        return compact_research_review_queue(page)

    def _query_research_decision_report(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.research_decision_report import (
            build_research_decision_report,
            compact_research_decision_report,
        )
        from services.research_detail_readiness import (
            compact_research_project_detail_readiness,
            get_research_project_detail_readiness,
        )

        project_id = _required_int(data, "project_id")
        report = build_research_decision_report(
            project_id,
            as_of=_optional_str(data.get("as_of")),
        )
        compact = compact_research_decision_report(report)
        compact["detail_evidence_readiness"] = compact_research_project_detail_readiness(
            get_research_project_detail_readiness(project_id)
        )
        return compact

    def _query_market_niches(self, data: dict[str, Any]) -> Any:
        from services.market_niches import fetch_market_niches_page, get_market_niche

        niche_id = _optional_int(data.get("niche_id"))
        if niche_id is not None:
            return get_market_niche(niche_id)
        page = fetch_market_niches_page(
            limit=_int(data.get("limit"), 50, 1, 200),
            offset=0,
            marketplace=_optional_str(data.get("marketplace")) or "US",
            status=_optional_str(data.get("status")),
            keyword=_optional_str(data.get("keyword")),
            sort_by="updated_at",
            sort_dir="desc",
        )
        return page["rows"]

    def _query_competitive_graph(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.competitive_graph import get_competitive_graph

        return get_competitive_graph(
            _required_int(data, "niche_id"),
            snapshot_id=_optional_int(data.get("snapshot_id")),
            limit=_int(data.get("limit"), 50, 10, 100),
            min_shared=_int(data.get("min_shared"), 1, 1, 100_000),
            focus_asin=_optional_str(data.get("focus_asin")),
        )

    def _query_scoring_v2_replay(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.scoring_v2 import fetch_scoring_v2_replay

        page = fetch_scoring_v2_replay(
            limit=_int(data.get("limit"), 10, 1, 20),
            offset=0,
            marketplace=_optional_str(data.get("marketplace")) or "US",
            keyword=_optional_str(data.get("keyword")),
            strategy=_optional_str(data.get("strategy")) or "balanced",
            recommendation=_optional_str(data.get("recommendation")),
            min_confidence=_optional_float(data.get("min_confidence")),
            max_risk=_optional_float(data.get("max_risk")),
            sort_by=_optional_str(data.get("sort_by")) or "opportunity_score",
            sort_dir=_optional_str(data.get("sort_dir")) or "desc",
        )
        compact_rows = []
        for row in page.get("rows", []):
            trend = row.get("trend") or {}
            source = row.get("source_identity") or {}
            compact_rows.append(
                {
                    "asin": row.get("asin"),
                    "title": row.get("title"),
                    "keyword": row.get("keyword"),
                    "snapshot_at": row.get("snapshot_at"),
                    "price": row.get("price"),
                    "rating": row.get("rating"),
                    "review_count": row.get("review_count"),
                    "monthly_bought": row.get("monthly_bought"),
                    "organic_rank": row.get("organic_rank"),
                    "legacy_total_score": row.get("legacy_total_score"),
                    "opportunity_score": row.get("opportunity_score"),
                    "risk_score": row.get("risk_score"),
                    "confidence_score": row.get("confidence_score"),
                    "confidence_level": row.get("confidence_level"),
                    "recommendation": row.get("recommendation_label"),
                    "recommendation_reason": row.get("recommendation_reason"),
                    "supporting_reasons": row.get("supporting_reasons"),
                    "opposing_reasons": row.get("opposing_reasons"),
                    "trend": {
                        "sample_size": trend.get("sample_size"),
                        "span_days": trend.get("span_days"),
                        "confidence": trend.get("confidence"),
                        "growth_score": trend.get("growth_score"),
                    },
                    "source": {
                        "model_version": source.get("model_version"),
                        "strategy": source.get("strategy"),
                        "rank_snapshot_id": source.get("rank_snapshot_id"),
                        "product_snapshot_id": source.get("product_snapshot_id"),
                    },
                }
            )
        return {
            "rows": compact_rows,
            "total": page.get("total"),
            "summary": page.get("summary"),
            "strategy": page.get("strategy"),
            "model": {
                "version": (page.get("model") or {}).get("version"),
                "status": (page.get("model") or {}).get("status"),
                "boundaries": (page.get("model") or {}).get("boundaries"),
            },
        }

    def _query_scoring_v2_calibration(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.scoring_v2 import fetch_scoring_v2_calibration

        report = fetch_scoring_v2_calibration(
            sample_per_bucket=_int(data.get("sample_per_bucket"), 1, 1, 3),
            marketplace=_optional_str(data.get("marketplace")) or "US",
            keyword=_optional_str(data.get("keyword")),
            strategy=_optional_str(data.get("strategy")) or "balanced",
        )
        rows = []
        for row in report.get("rows", []):
            rows.append(
                {
                    "sample_key": row.get("sample_key"),
                    "bucket": row.get("sample_bucket_label"),
                    "asin": row.get("asin"),
                    "title": row.get("title"),
                    "keyword": row.get("keyword"),
                    "opportunity_score": row.get("opportunity_score"),
                    "risk_score": row.get("risk_score"),
                    "confidence_score": row.get("confidence_score"),
                    "recommendation": row.get("recommendation_label"),
                    "strategy_outcomes": row.get("strategy_outcomes"),
                    "audit_flags": [
                        {
                            "code": flag.get("code"),
                            "label": flag.get("label"),
                            "reason": flag.get("reason"),
                        }
                        for flag in row.get("calibration_flags", [])
                    ],
                    "source": {
                        "model_version": (row.get("source_identity") or {}).get("model_version"),
                        "rank_snapshot_id": (row.get("source_identity") or {}).get("rank_snapshot_id"),
                        "product_snapshot_id": (row.get("source_identity") or {}).get("product_snapshot_id"),
                    },
                }
            )
        return {
            "rows": rows,
            "summary": report.get("summary"),
            "strategy": report.get("strategy"),
            "calibration": report.get("calibration"),
            "model": {
                "version": (report.get("model") or {}).get("version"),
                "status": (report.get("model") or {}).get("status"),
                "boundaries": (report.get("model") or {}).get("boundaries"),
            },
        }

    def _query_review_insights(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return self.controller.get_review_insights(
            limit=_int(data.get("limit"), 100, 1, 500),
            keyword=_optional_str(data.get("keyword")),
        )

    def _query_tracking_tasks(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return self._tracking_tasks(
            status=_optional_str(data.get("status")),
            limit=_int(data.get("limit"), 50, 1, 500),
        )

    def _query_tracking_evidence(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.tracking_evidence import (
            build_tracking_task_evidence,
            compact_tracking_task_evidence,
        )

        movement_limit = _int(data.get("movement_limit"), 5, 1, 10)
        evidence = build_tracking_task_evidence(
            _required_int(data, "task_id"),
            movement_limit=movement_limit,
        )
        return compact_tracking_task_evidence(
            evidence,
            movement_limit=movement_limit,
        )

    def _tracking_tasks(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        controller_method = getattr(self.controller, "get_tracking_tasks", None)
        if callable(controller_method):
            return controller_method(status=status, limit=limit)
        from services.keyword_tracking import list_tracking_tasks

        tasks = list_tracking_tasks(
            status=status,
            limit=limit,
        )
        return [task.to_dict() for task in tasks]

    def _query_tasks(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return self.controller.get_task_jobs(
            limit=_int(data.get("limit"), 100, 1, 500),
            status=_optional_str(data.get("status")),
        )

    def _open_amazon_page(self, _data: dict[str, Any]) -> dict[str, Any]:
        return self.controller.open_amazon_page()

    def _create_keyword_tracking(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.keyword_tracking import create_tracking_task

        raw_pages = data.get("pages_per_keyword")
        task = create_tracking_task(
            keyword=_required_str(data, "keyword"),
            marketplace=_optional_str(data.get("marketplace")) or "US",
            target_snapshots=_int(data.get("target_snapshots"), 3, 1, 365),
            pages_per_keyword=_int(raw_pages, 2, 1, 7) if raw_pages is not None else None,
        )
        return task.to_dict()

    def _set_keyword_tracking_status(self, data: dict[str, Any]) -> dict[str, Any]:
        from services.keyword_tracking import update_tracking_task_status

        task = update_tracking_task_status(
            _required_int(data, "task_id"),
            _required_str(data, "status"),
        )
        return task.to_dict()

    def _trigger_collection(self, data: dict[str, Any]) -> Any:
        from services.keyword_tracking_scheduler import run_keyword_tracking_scheduler

        # M0 红线：模型传入的 execute 一律忽略；只有确认通过后才会调用本 handler。
        return run_keyword_tracking_scheduler(
            execute=True,
            task_id=_optional_int(data.get("task_id")),
            controller=self.controller,
        )


def get_readonly_tool_definitions() -> list[AgentToolDefinition]:
    return [
        AgentToolDefinition(
            name="query_app_overview",
            description=(
                "查询应用全局概览：推荐榜、关键词机会、一级关键词分组、关键词创意候选、评论洞察、"
                "追踪任务和任务中心。适合回答“现在系统情况/下一步/哪些机会值得看”。"
            ),
            parameters=_schema(
                {
                    "limit": _integer("每个模块返回数量，默认 5，最大 20。", default=5, minimum=1, maximum=20),
                    "keyword": _string("可选关键词，用于过滤关键词机会和评论洞察。"),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_recommendations",
            description="查询当前推荐榜商品，适合回答哪些商品值得优先关注。",
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 50。", default=50, minimum=1, maximum=200),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_products",
            description=(
                "查询商品池，可按关键词、综合分、价格、评分、评论数、近月购买、自然序位、促销和尺寸完整性筛选。"
                "按已入库关键词精确查询时，默认只返回该词最新完整采集批次；"
                "只有明确要求历史曾观察商品时才使用 observed。"
            ),
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 100。", default=100, minimum=1, maximum=500),
                    "keyword": _string("商品标题/ASIN/关键词过滤，可为空。"),
                    "keyword_exact": {
                        "type": "boolean",
                        "description": "是否按已入库关键词精确筛选；分析某个关键词当前商品时设为 true。",
                        "default": False,
                    },
                    "keyword_scope": {
                        "type": "string",
                        "description": "精确关键词范围：current=最新完整批次，observed=历史曾观察并集。",
                        "enum": ["current", "observed"],
                        "default": "current",
                    },
                    "min_score": _number("最低综合得分。"),
                    "max_score": _number("最高综合得分。"),
                    "min_price": _number("最低价格。"),
                    "max_price": _number("最高价格。"),
                    "min_rating": _number("最低评分。"),
                    "max_rating": _number("最高评分。"),
                    "min_reviews": _integer("最小评论数。", minimum=0),
                    "max_reviews": _integer("最大评论数，用于筛低竞争商品。", minimum=0),
                    "min_bought": _integer("最小近月购买量。", minimum=0),
                    "max_bought": _integer("最大近月购买量。", minimum=0),
                    "min_rank": _integer("最小自然序位估算。", minimum=1),
                    "max_rank": _integer("最大自然序位估算。", minimum=1),
                    "deal_status": {
                        "type": "string",
                        "description": "促销状态。",
                        "enum": ["all", "deal", "regular"],
                        "default": "all",
                    },
                    "size_status": {
                        "type": "string",
                        "description": "尺寸字段完整性。",
                        "enum": ["all", "known", "missing"],
                        "default": "all",
                    },
                }
            ),
        ),
        AgentToolDefinition(
            name="query_product_detail",
            description="按 ASIN 查询商品详情、最新评分、历史快照，以及已采集的类目、Amazon 首次可售日期和多类目 BSR。",
            parameters=_schema({"asin": _string("Amazon ASIN。")}, required=["asin"]),
        ),
        AgentToolDefinition(
            name="query_product_metrics",
            description=(
                "按 ASIN 查询指标与估算中心：报价履约、物理规格、SERP 市场证据、"
                "确定性指标、卖家精确输入、经验情景和数据质量。必须区分事实、计算与估算。"
            ),
            parameters=_schema({"asin": _string("Amazon ASIN。")}, required=["asin"]),
        ),
        AgentToolDefinition(
            name="query_detail_evidence_priorities",
            description=(
                "只读查询下一批更值得补采详情的商品，按进行中研究项目角色、人工观察计划、"
                "最近搜索观察和详情证据缺口做全结果优先排序，并结合本地 HTML 区分首次采集、"
                "过期刷新、本地回填、解析适配和页面未提供；这是无数值评分的人工行动队列。"
                "页面未提供不等于 0，也不表示应连续重采。工具不创建任务、不启动浏览器、不采集、不写库。"
            ),
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 10，最大 50。", default=10, minimum=1, maximum=50),
                    "stale_days": _integer("详情证据过期天数，默认 30。", default=30, minimum=1, maximum=3650),
                    "priority": {
                        "type": "string",
                        "description": "优先队列筛选。",
                        "enum": ["all", "focus", "project", "planned", "recent", "routine"],
                        "default": "all",
                    },
                    "project_id": _integer(
                        "可选研究项目 ID；传入后，成员范围、计数、排序和分页只针对该项目。",
                        minimum=1,
                    ),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_product_trend",
            description="按 ASIN 查询商品趋势置信度和关键指标变化。",
            parameters=_schema({"asin": _string("Amazon ASIN。")}, required=["asin"]),
        ),
        AgentToolDefinition(
            name="query_keyword_opportunities",
            description=(
                "查询关键词机会聚合，当前市场指标只使用每个关键词最新完整采集批次；"
                "同时返回历史观察商品数、上一批、留存、进入、退出和排名变化证据。"
            ),
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 100。", default=100, minimum=1, maximum=500),
                    "keyword": _string("关键词过滤，可为空。"),
                    "min_products": _integer("最少关联商品数。", minimum=0),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_keyword_groups",
            description=(
                "按一级关键词聚合关键词机会，支持词尾、词首、共享词三种模式；"
                "适合分析赛道/中心词，而不是只看单个关键词。"
            ),
            parameters=_schema(
                {
                    "mode": {
                        "type": "string",
                        "description": "分组方式：tail=词尾，first=词首，shared=共享词。",
                        "enum": ["tail", "first", "shared"],
                    },
                    "limit": _integer("用于分组的关键词数量，默认 500，最大 500。", default=500, minimum=1, maximum=500),
                    "keyword": _string("关键词过滤，可为空。"),
                    "min_products": _integer("最少关联商品数。", minimum=0),
                    "max_groups": _integer("返回一级分组数，默认 20。", default=20, minimum=1, maximum=100),
                    "keywords_per_group": _integer("每组返回二级关键词数，默认 8。", default=8, minimum=1, maximum=30),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_keyword_ideas",
            description="查询关键词创意工坊候选池，适合查看种子词扩展、来源证据、创意分和候选状态。",
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 100。", default=100, minimum=1, maximum=500),
                    "marketplace": _string("站点，默认 US。"),
                    "status": {
                        "type": "string",
                        "description": "候选状态，可为空。",
                        "enum": ["candidate", "promoted", "tracking", "ignored"],
                    },
                    "keyword": _string("关键词过滤，可为空。"),
                    "source": {
                        "type": "string",
                        "description": "来源过滤，可为空。",
                        "enum": ["amazon_suggest", "title_ngram", "existing_keyword"],
                    },
                }
            ),
        ),
        AgentToolDefinition(
            name="query_research_projects",
            description=(
                "只读查询研究项目及其阶段、关联商品/关键词数量、证据覆盖和人工结论；"
                "适合回答哪些选品方向正在验证、缺什么证据、哪些已批准或拒绝。"
            ),
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 50。", default=50, minimum=1, maximum=200),
                    "marketplace": _string("站点，默认 US。"),
                    "status": {
                        "type": "string",
                        "description": "项目状态，可为空。",
                        "enum": ["idea", "collecting", "validating", "candidate", "manual_review", "approved", "rejected"],
                    },
                    "keyword": _string("项目名称、目标或策略关键词，可为空。"),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_research_review_queue",
            description=(
                "只读查询研究项目复核队列，综合证据时效、趋势门槛、核心词追踪任务、冻结报告基线和人工观察计划，"
                "区分需要处理、等待证据与终态稳定；趋势使用合格时间点，原始点仅供审计。"
                "观察计划到期只表示该由用户人工查看。工具不保存计划、不完成复核、不创建任务、不采集、"
                "不冻结报告，也不修改项目状态。"
            ),
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 25，最大 100。", default=25, minimum=1, maximum=100),
                    "marketplace": _string("站点，默认 US。"),
                    "status": {
                        "type": "string",
                        "description": "项目状态，可为空。",
                        "enum": ["idea", "collecting", "validating", "candidate", "manual_review", "approved", "rejected"],
                    },
                    "keyword": _string("项目名称、目标或策略关键词，可为空。"),
                    "attention": {
                        "type": "string",
                        "description": "关注分组：需要处理、等待证据或终态稳定。",
                        "enum": ["action_required", "waiting", "terminal"],
                    },
                    "monitoring": {
                        "type": "string",
                        "description": "人工观察计划筛选；到期不代表后台任务已调度。",
                        "enum": ["active", "due", "paused", "unplanned"],
                    },
                    "as_of": _string("可选评估日期 YYYY-MM-DD；用于复现证据时效判断。"),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_research_decision_report",
            description=(
                "只读生成指定研究项目的决策门禁报告，返回需求、竞争、差异化、趋势、财务、"
                "证据质量、支持理由、反对理由、数据缺口和来源指纹；趋势同时区分原始点、"
                "合格点、排名完整性、独立观察窗口与新鲜度，并逐商品返回详情证据准备度与处置建议。"
                "就绪度不是机会分，工具不能替用户批准/淘汰项目或自动补齐证据。"
            ),
            parameters=_schema(
                {
                    "project_id": _integer("研究项目 ID。", minimum=1),
                    "as_of": _string("可选评估日期 YYYY-MM-DD；用于复现同一日期的时效判断。"),
                },
                required=["project_id"],
            ),
        ),
        AgentToolDefinition(
            name="query_market_niches",
            description=(
                "只读查询市场利基及其成员关键词、人工锚点商品、研究项目关系和最新证据快照；"
                "适合解释跨关键词去重市场、覆盖缺口、价格/评论分布、广告密度与集中度。"
                "证据等级不是机会评分，不可据此自动作进入结论。"
            ),
            parameters=_schema(
                {
                    "niche_id": _integer("可选利基 ID；提供后返回完整详情。", minimum=1),
                    "limit": _integer("列表返回数量，默认 50。", default=50, minimum=1, maximum=200),
                    "marketplace": _string("站点，默认 US。"),
                    "status": {
                        "type": "string",
                        "description": "利基状态，可为空。",
                        "enum": ["draft", "active", "archived"],
                    },
                    "keyword": _string("利基名称、定义或类目范围关键词，可为空。"),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_competitive_graph",
            description=(
                "只读查询指定利基快照的关键词-ASIN共现、关键词相似度、竞品覆盖和目标ASIN关键词缺口。"
                "所有关系只代表已采集页面观察；自然可见度是位次代理，不是流量，样本置信也不是机会评分。"
            ),
            parameters=_schema(
                {
                    "niche_id": _integer("市场利基 ID。", minimum=1),
                    "snapshot_id": _integer("可选利基快照 ID；不传使用最新快照。", minimum=1),
                    "limit": _integer("返回竞品数量，默认 50。", default=50, minimum=10, maximum=100),
                    "min_shared": _integer("关键词关系最少共同 ASIN 数，默认 1。", default=1, minimum=1),
                    "focus_asin": _string("可选目标 ASIN，用于分析成员关键词缺口。"),
                },
                required=["niche_id"],
            ),
        ),
        AgentToolDefinition(
            name="query_scoring_v2_replay",
            description=(
                "只读查询 P7 评分 V2 影子回放，分别返回机会分、风险分和置信度，并与旧综合分并列。"
                "结果按商品与关键词最新完整采集批次计算，不写 product_scores，也不代表已批准进入。"
            ),
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 10，最大 20。", default=10, minimum=1, maximum=20),
                    "marketplace": _string("站点，默认 US。"),
                    "keyword": _string("可按关键词、ASIN 或标题过滤。"),
                    "strategy": {
                        "type": "string",
                        "description": "研究策略模板。",
                        "enum": ["balanced", "low_budget", "differentiation", "trend"],
                    },
                    "recommendation": {
                        "type": "string",
                        "description": "影子建议状态，可为空。",
                        "enum": ["priority_validate", "observe", "benchmark_only", "pause"],
                    },
                    "min_confidence": _number("最低置信度，0-100。"),
                    "max_risk": _number("最高风险分，0-100。"),
                    "sort_by": {
                        "type": "string",
                        "description": "排序字段。",
                        "enum": ["opportunity_score", "risk_score", "confidence_score", "legacy_total_score"],
                    },
                    "sort_dir": {
                        "type": "string",
                        "description": "排序方向。",
                        "enum": ["asc", "desc"],
                    },
                }
            ),
        ),
        AgentToolDefinition(
            name="query_scoring_v2_calibration",
            description=(
                "只读查询 P7.1 评分影子模型的建议×置信度分层校准样本、跨策略分歧和审计标记。"
                "审计标记只表示优先人工检查，不是错误标签；工具不写人工判断、不调权，也不写数据库。"
            ),
            parameters=_schema(
                {
                    "sample_per_bucket": _integer("每个实际分层返回数量，默认 1，最大 3。", default=1, minimum=1, maximum=3),
                    "marketplace": _string("站点，默认 US。"),
                    "keyword": _string("可按关键词、ASIN 或标题缩小校准范围。"),
                    "strategy": {
                        "type": "string",
                        "description": "作为分层主视角的研究策略。",
                        "enum": ["balanced", "low_budget", "differentiation", "trend"],
                    },
                }
            ),
        ),
        AgentToolDefinition(
            name="query_review_insights",
            description="查询评论洞察和低分痛点摘要。",
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 100。", default=100, minimum=1, maximum=500),
                    "keyword": _string("标题关键词，可为空。"),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_tracking_tasks",
            description="查询关键词追踪任务列表。操作类工具前应先调用本工具拿 task_id。",
            parameters=_schema(
                {
                    "status": _string("任务状态：active、paused、completed、error，可为空。"),
                    "limit": _integer("返回数量，默认 50。", default=50, minimum=1, maximum=500),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_tracking_evidence",
            description=(
                "只读查询一个关键词追踪任务的证据复盘：区分原始进度与合格趋势时点，返回安全间隔、"
                "14/30 天门槛、相邻批次变化、研究候选/对标观察及本地证据摘要。"
                "不刷新任务、不联网、不采集、不写库；未观察到商品不等于下架或长期衰退。"
            ),
            parameters=_schema(
                {
                    "task_id": _integer("关键词追踪任务 ID。", minimum=1),
                    "movement_limit": _integer(
                        "相邻批次各类明细最多返回数量，默认 5，最大 10。",
                        default=5,
                        minimum=1,
                        maximum=10,
                    ),
                },
                required=["task_id"],
            ),
        ),
        AgentToolDefinition(
            name="query_tasks",
            description="查询任务中心采集/导入任务运行状态。",
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 100。", default=100, minimum=1, maximum=500),
                    "status": _string("任务状态，可为空。"),
                }
            ),
        ),
    ]


def get_operation_tool_definitions() -> list[AgentToolDefinition]:
    return [
        AgentToolDefinition(
            name="open_amazon_page",
            description=(
                "预开启 Amazon 首页，供用户手动处理地址、登录、验证码或页面状态；"
                "不采集、不入库，但会联网打开共享浏览器会话，必须先向用户确认。"
            ),
            parameters=_schema({}),
            requires_confirmation=True,
        ),
        AgentToolDefinition(
            name="create_keyword_tracking",
            description="创建关键词追踪任务。写入 MySQL，必须先向用户确认，不能自动执行。",
            parameters=_schema(
                {
                    "keyword": _string("要追踪的关键词。"),
                    "target_snapshots": _integer("目标快照次数，默认 3。", default=3, minimum=1, maximum=365),
                    "marketplace": _string("站点，默认 US。"),
                    "pages_per_keyword": _integer(
                        "每轮采集页数；不填则使用设置默认值，最大 7。",
                        minimum=1,
                        maximum=7,
                    ),
                },
                required=["keyword"],
            ),
            requires_confirmation=True,
        ),
        AgentToolDefinition(
            name="set_keyword_tracking_status",
            description="修改关键词追踪任务状态。写入 MySQL，必须先向用户确认，不能自动执行。",
            parameters=_schema(
                {
                    "task_id": _integer("关键词追踪任务 ID。", minimum=1),
                    "status": {
                        "type": "string",
                        "description": "目标状态。",
                        "enum": ["active", "paused", "completed", "error"],
                    },
                },
                required=["task_id", "status"],
            ),
            requires_confirmation=True,
        ),
        AgentToolDefinition(
            name="trigger_collection",
            description=(
                "触发关键词追踪采集。会联网打开浏览器抓取 Amazon，必须先向用户确认；"
                "execute 参数由后端按确认结果强制控制，模型不能绕过。"
            ),
            parameters=_schema(
                {
                    "task_id": _integer("可选任务 ID；不传表示检查所有到期任务。", minimum=1),
                }
            ),
            requires_confirmation=True,
        ),
    ]


def get_agent_tool_definitions() -> list[AgentToolDefinition]:
    return get_readonly_tool_definitions() + get_operation_tool_definitions()


def get_agent_tool_schemas() -> list[dict[str, Any]]:
    return [tool.to_provider_schema() for tool in get_agent_tool_definitions()]


def get_confirmation_tool_names() -> set[str]:
    return {tool.name for tool in get_operation_tool_definitions() if tool.requires_confirmation}


def get_readonly_tool_schemas() -> list[dict[str, Any]]:
    return [tool.to_provider_schema() for tool in get_readonly_tool_definitions()]


def _schema(properties: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _integer(
    description: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer", "description": description}
    if default is not None:
        schema["default"] = default
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def _number(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}


def _required_str(data: dict[str, Any], key: str) -> str:
    value = _optional_str(data.get(key))
    if not value:
        raise ValueError(f"工具参数缺少 {key}")
    return value


def _required_int(data: dict[str, Any], key: str) -> int:
    value = _optional_int(data.get(key))
    if value is None:
        raise ValueError(f"工具参数缺少 {key}")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return _int(value, 0, 0, 10_000_000)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _enum(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _safe_call(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return {"ok": True, "data": _json_safe(fn())}
    except Exception as exc:
        return {"ok": False, "data": None, "message": str(exc)}


_KW_STOPWORDS = {"for", "the", "a", "an", "of", "with", "and", "to", "in", "on", "by", "my", "your"}


def _keyword_tokens(keyword: Any) -> list[str]:
    return [part for part in str(keyword or "").lower().strip().split() if part]


def _keyword_norm(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _keyword_group_key(keyword: Any, mode: str, freq: dict[str, int]) -> str:
    tokens = [_keyword_norm(token) for token in _keyword_tokens(keyword)]
    if not tokens:
        return ""
    if mode == "first":
        return tokens[0]
    if mode == "shared":
        candidates = [token for token in tokens if token not in _KW_STOPWORDS] or tokens
        return max(candidates, key=lambda token: (freq.get(token, 0), token))
    return tokens[-1]


def _build_keyword_groups(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    max_groups: int,
    keywords_per_group: int,
) -> list[dict[str, Any]]:
    effective_mode = mode if mode in {"tail", "first", "shared"} else "tail"
    freq: dict[str, int] = {}
    if effective_mode == "shared":
        for row in rows:
            for token in set(_keyword_norm(token) for token in _keyword_tokens(row.get("keyword"))):
                freq[token] = freq.get(token, 0) + 1

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = _keyword_group_key(row.get("keyword"), effective_mode, freq)
        if not key:
            continue
        groups.setdefault(key, []).append(row)

    result: list[dict[str, Any]] = []
    for primary, items in groups.items():
        scores = [_float_value(item.get("opportunity_score")) for item in items]
        product_count = sum(_int_value(item.get("product_count")) for item in items)
        sorted_items = sorted(items, key=lambda item: _float_value(item.get("opportunity_score")), reverse=True)
        avg_score = sum(scores) / len(scores) if scores else 0.0
        max_score = max(scores) if scores else 0.0
        result.append(
            {
                "primary": primary,
                "keyword_count": len(items),
                "product_count": product_count,
                "avg_opportunity_score": round(avg_score, 2),
                "max_opportunity_score": round(max_score, 2),
                "level": "蓝海赛道" if avg_score >= 70 else "可观察",
                "keywords": [
                    {
                        "keyword": item.get("keyword"),
                        "opportunity_score": item.get("opportunity_score"),
                        "product_count": item.get("product_count"),
                    }
                    for item in sorted_items[:keywords_per_group]
                ],
            }
        )
    return sorted(
        result,
        key=lambda group: (
            _float_value(group.get("avg_opportunity_score")),
            _int_value(group.get("keyword_count")),
            _int_value(group.get("product_count")),
        ),
        reverse=True,
    )[:max_groups]


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value
