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
            "query_product_trend": self._query_product_trend,
            "query_keyword_opportunities": self._query_keyword_opportunities,
            "query_keyword_groups": self._query_keyword_groups,
            "query_keyword_ideas": self._query_keyword_ideas,
            "query_review_insights": self._query_review_insights,
            "query_tracking_tasks": self._query_tracking_tasks,
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
        return self.controller.get_product_pool(
            limit=_int(data.get("limit"), 100, 1, 500),
            keyword=_optional_str(data.get("keyword")),
            min_score=_optional_float(data.get("min_score")),
            min_price=_optional_float(data.get("min_price")),
            max_price=_optional_float(data.get("max_price")),
            max_reviews=_optional_int(data.get("max_reviews")),
        )

    def _query_product_detail(self, data: dict[str, Any]) -> dict[str, Any]:
        return self.controller.get_product_history(_required_str(data, "asin"))

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
            description="查询商品池，可按关键词、综合分、价格和最大评论数筛选。",
            parameters=_schema(
                {
                    "limit": _integer("返回数量，默认 100。", default=100, minimum=1, maximum=500),
                    "keyword": _string("标题关键词，可为空。"),
                    "min_score": _number("最低综合得分。"),
                    "min_price": _number("最低价格。"),
                    "max_price": _number("最高价格。"),
                    "max_reviews": _integer("最大评论数，用于筛低竞争商品。", minimum=0),
                }
            ),
        ),
        AgentToolDefinition(
            name="query_product_detail",
            description="按 ASIN 查询商品详情、最新评分、历史快照，以及已采集的类目、Amazon 首次可售日期和多类目 BSR。",
            parameters=_schema({"asin": _string("Amazon ASIN。")}, required=["asin"]),
        ),
        AgentToolDefinition(
            name="query_product_trend",
            description="按 ASIN 查询商品趋势置信度和关键指标变化。",
            parameters=_schema({"asin": _string("Amazon ASIN。")}, required=["asin"]),
        ),
        AgentToolDefinition(
            name="query_keyword_opportunities",
            description="查询关键词机会聚合，适合发现需求、竞争和机会评分。",
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
