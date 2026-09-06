"""Evidence-bound decision report for one research project."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from typing import Any, Iterable

from database.mysql_client import MySQLClient
from services.market_niches import evidence_level, niche_snapshot_source_timing
from services.research_workspace import load_research_project_bundle


REPORT_SCHEMA_VERSION = "1.2"
REPORT_METHOD_VERSION = "research-decision-report-v1.3"
TREND_MIN_POINTS = 3
TREND_MIN_DAYS = 14
TREND_STABLE_POINTS = 6
TREND_STABLE_DAYS = 30
TREND_INDEPENDENT_WINDOW_HOURS = 24

READINESS_LABELS = {
    "not_started": "尚未形成研究样本",
    "evidence_building": "证据建设中",
    "reviewable": "可进入人工复核",
    "decision_ready": "具备人工决策基础",
}
AXIS_STATUS_LABELS = {
    "supporting": "形成支持信号",
    "mixed": "信号混合",
    "risk": "存在明显压力",
    "insufficient": "证据不足",
}
CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}
SEVERITY_LABELS = {"blocker": "阻断", "high": "高", "medium": "中", "low": "低"}
FINAL_STAGE_STATUSES = {"candidate", "manual_review", "approved", "rejected"}


class ResearchDecisionReportError(ValueError):
    """Raised when a decision report request cannot be evaluated."""


def build_research_decision_report(
    project_id: int,
    *,
    as_of: date | str | None = None,
    client: MySQLClient | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a transactionally consistent, read-only project report."""
    project_id_value = _positive_int(project_id, "研究项目 ID")
    evaluated_on = parse_report_date(as_of)
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            return build_research_decision_report_from_cursor(
                cursor,
                project_id_value,
                evaluated_on=evaluated_on,
                generated_at=generated_at,
            )


def build_research_decision_report_from_cursor(
    cursor: Any,
    project_id: int,
    *,
    evaluated_on: date | str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a report inside the caller's transaction."""
    project_id_value = _positive_int(project_id, "研究项目 ID")
    detail = load_research_project_bundle(cursor, project_id_value)
    supplement = _fetch_report_supplement(cursor, project_id_value)
    return assemble_research_decision_report(
        detail,
        supplement=supplement,
        evaluated_on=evaluated_on,
        generated_at=generated_at,
    )


def assemble_research_decision_report(
    detail: dict[str, Any],
    *,
    supplement: dict[str, Any] | None = None,
    evaluated_on: date | str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Pure report assembly used by production and deterministic tests."""
    if not isinstance(detail, dict) or not isinstance(detail.get("project"), dict):
        raise ResearchDecisionReportError("研究项目数据不完整，无法生成决策报告")
    evaluated_date = parse_report_date(evaluated_on)
    generated = generated_at or datetime.now().astimezone()
    extra = supplement or {}
    project = _json_safe(dict(detail["project"]))
    products = _merge_rows(
        detail.get("products") or [],
        extra.get("products") or [],
        key="product_id",
    )
    keywords = _normalize_keywords(
        detail.get("keywords") or [],
        extra.get("keywords") or [],
        evaluated_on=evaluated_date,
    )
    niches = _normalize_niches(detail.get("niches") or [], extra.get("niches") or [])
    notes = [_json_safe(dict(row)) for row in detail.get("notes") or []]
    notes_by_type = _notes_by_type(notes)
    freshness = _build_freshness(products, keywords, niches, evaluated_date)
    timeline = _build_timeline_summary(
        products,
        keywords,
        niches,
        evaluated_on=evaluated_date,
    )
    primary_niche = _select_primary_niche(niches)
    gates = _build_gates(
        project=project,
        products=products,
        keywords=keywords,
        niches=niches,
        notes_by_type=notes_by_type,
        timeline=timeline,
        primary_niche=primary_niche,
    )
    readiness = _build_readiness(project, gates, products, keywords, niches)
    axes = _build_axes(
        project=project,
        products=products,
        keywords=keywords,
        notes_by_type=notes_by_type,
        primary_niche=primary_niche,
        timeline=timeline,
        freshness=freshness,
    )
    arguments = _build_arguments(axes, notes_by_type)
    gaps = _build_data_gaps(
        project=project,
        gates=gates,
        freshness=freshness,
        products=products,
        primary_niche=primary_niche,
    )
    due_diligence = _build_due_diligence(products)
    source_manifest = _build_source_manifest(
        project=project,
        products=products,
        keywords=keywords,
        niches=niches,
        notes=notes,
    )
    evidence_fingerprint = _fingerprint(source_manifest)
    evidence_as_of = freshness.get("latest_source_at")
    report: dict[str, Any] = {
        "report_type": "research_project_decision_report",
        "schema_version": REPORT_SCHEMA_VERSION,
        "method_version": REPORT_METHOD_VERSION,
        "generated_at": generated.isoformat(timespec="seconds"),
        "evaluated_on": evaluated_date.isoformat(),
        "evidence_as_of": evidence_as_of,
        "evidence_fingerprint": evidence_fingerprint,
        "project": _project_summary(project),
        "readiness": readiness,
        "evidence_health": {
            "project_evidence_coverage": _number(project.get("evidence_coverage")) or 0.0,
            "product_count": len(products),
            "keyword_count": len(keywords),
            "niche_count": len(niches),
            "note_count": len(notes),
            "freshness": freshness,
            "timeline": timeline,
        },
        "decision_axes": axes,
        "supporting_arguments": arguments["supporting"],
        "counter_arguments": arguments["counter"],
        "data_gaps": gaps,
        "operational_due_diligence": due_diligence,
        "assets": {
            "products": [_compact_product(row) for row in products],
            "keywords": [_compact_keyword(row) for row in keywords],
            "niches": [_compact_niche(row) for row in niches],
            "notes": notes,
        },
        "source_manifest": source_manifest,
        "guardrails": {
            "human_decision_required": True,
            "automatic_status_change": False,
            "automatic_collection": False,
            "score_integration": "disabled",
            "statement": (
                "本报告只判断证据是否足以进入人工复核，不替代供应链、合规、利润和账号侧真实数据，"
                "也不自动给出通过或淘汰结论。"
            ),
            "prohibited_inferences": [
                "近月购买量是页面可见下界代理，不是真实销量。",
                "BSR 未换算为销量，排名和自然可见度代理都不等于流量。",
                "SERP 与竞品关系只代表已采集页面观察，不代表 Amazon 全市场。",
                "决策就绪度不是机会分，也不进入现有综合评分。",
                "原始采集次数不等于合格趋势时间点；排名异常或不足 24 小时间隔的批次不单独通过门槛。",
                "少于 3 个合格时间点或 14 个完整自然日跨度时，不形成趋势结论；V1 不判断季节性。",
            ],
        },
        "methodology": _methodology(),
    }
    fingerprint_payload = {
        key: value
        for key, value in report.items()
        if key not in {"generated_at", "report_fingerprint"}
    }
    report["report_fingerprint"] = _fingerprint(fingerprint_payload)
    return report


def compact_research_decision_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return the evidence-rich subset appropriate for the in-app Agent."""
    return {
        key: report.get(key)
        for key in (
            "report_type",
            "schema_version",
            "method_version",
            "generated_at",
            "evaluated_on",
            "evidence_as_of",
            "evidence_fingerprint",
            "report_fingerprint",
            "project",
            "readiness",
            "evidence_health",
            "decision_axes",
            "supporting_arguments",
            "counter_arguments",
            "data_gaps",
            "operational_due_diligence",
            "guardrails",
        )
    }


def export_research_decision_report(
    report: dict[str, Any],
    export_format: str,
) -> tuple[bytes, str, str]:
    fmt = str(export_format or "").strip().lower()
    project_id = _positive_int((report.get("project") or {}).get("id"), "研究项目 ID")
    evaluated_on = str(report.get("evaluated_on") or date.today().isoformat()).replace("-", "")
    short_hash = str(report.get("report_fingerprint") or "")[:12] or "unverified"
    stem = f"research_project_{project_id}_{evaluated_on}_{short_hash}"
    if fmt == "json":
        content = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        return content, f"{stem}.json", "application/json; charset=utf-8"
    if fmt in {"markdown", "md"}:
        content = render_research_decision_markdown(report).encode("utf-8")
        return content, f"{stem}.md", "text/markdown; charset=utf-8"
    raise ResearchDecisionReportError("导出格式仅支持 json 或 markdown")


def render_research_decision_markdown(report: dict[str, Any]) -> str:
    project = report.get("project") or {}
    readiness = report.get("readiness") or {}
    health = report.get("evidence_health") or {}
    freshness = health.get("freshness") or {}
    timeline = health.get("timeline") or {}
    lines = [
        f"# {project.get('name') or '研究项目'} - 选品决策报告",
        "",
        f"- 项目 ID：{project.get('id')}",
        f"- 站点：{project.get('marketplace') or '--'}",
        f"- 项目阶段：{project.get('status_label') or project.get('status') or '--'}",
        f"- 评估日期：{report.get('evaluated_on') or '--'}",
        f"- 证据截至：{report.get('evidence_as_of') or '--'}",
        f"- 方法版本：`{report.get('method_version') or '--'}`",
        f"- 证据指纹：`{report.get('evidence_fingerprint') or '--'}`",
        f"- 报告指纹：`{report.get('report_fingerprint') or '--'}`",
        "",
        "## 决策就绪度",
        "",
        f"**{readiness.get('label') or '--'}**，门禁通过 "
        f"{readiness.get('passed_count', 0)}/{readiness.get('total_count', 0)}。",
        "",
        readiness.get("summary") or "",
        "",
        "| 门禁 | 状态 | 最终决策要求 | 说明 |",
        "|---|---|---|---|",
    ]
    for gate in readiness.get("gates") or []:
        lines.append(
            f"| {_md(gate.get('label'))} | {'通过' if gate.get('passed') else '缺失'} | "
            f"{'是' if gate.get('critical_for_final_decision') else '否'} | {_md(gate.get('detail'))} |"
        )
    lines.extend(
        [
            "",
            "## 证据健康",
            "",
            f"- 关联商品：{health.get('product_count', 0)}",
            f"- 关联关键词：{health.get('keyword_count', 0)}",
            f"- 关联利基：{health.get('niche_count', 0)}",
            f"- 人工记录：{health.get('note_count', 0)}",
            f"- 来源新鲜度：{freshness.get('label') or '--'}",
            f"- 最强时间序列：{timeline.get('best_points', 0)} 个合格时间点"
            f"（原始 {timeline.get('best_raw_points', 0)} 个），跨度 "
            f"{timeline.get('best_span_days', 0)} 个完整自然日",
            f"- 时间点质量：{timeline.get('best_quality_label') or '--'}；"
            f"{timeline.get('best_freshness_label') or '--'}",
            "",
            "### 决策相关时间序列",
            "",
            "| 来源 | 角色 | 原始点 | 合格点 | 跨度 | 质量 | 新鲜度 |",
            "|---|---|---:|---:|---:|---|---|",
        ]
    )
    sequences = timeline.get("sequences") or []
    if sequences:
        for row in sequences:
            lines.append(
                f"| {_md(row.get('source_label'))} / {_md(row.get('name'))} | "
                f"{_md(row.get('role'))} | {_md(row.get('raw_points'))} | "
                f"{_md(row.get('points'))} | {_md(row.get('span_days'))} 天 | "
                f"{_md(row.get('quality_label'))} | {_md(row.get('freshness_label'))} |"
            )
    else:
        lines.append("| -- | -- | 0 | 0 | 0 天 | 无时间证据 | -- |")
    lines.extend(["", "## 五条决策轴线", ""])
    for axis in report.get("decision_axes") or []:
        lines.extend(
            [
                f"### {axis.get('label') or axis.get('key')}",
                "",
                f"- 判断：{axis.get('status_label') or axis.get('status')}",
                f"- 置信度：{axis.get('confidence_label') or axis.get('confidence')}",
                f"- 解释：{axis.get('summary') or '--'}",
            ]
        )
        for fact in axis.get("facts") or []:
            lines.append(f"- 证据：{fact.get('label')}：{fact.get('display')}")
        for caveat in axis.get("caveats") or []:
            lines.append(f"- 限制：{caveat}")
        lines.append("")
    lines.extend(["## 支持理由", ""])
    lines.extend(_argument_lines(report.get("supporting_arguments") or [], "暂无可核验支持理由。"))
    lines.extend(["", "## 反对理由与风险", ""])
    lines.extend(_argument_lines(report.get("counter_arguments") or [], "暂无已记录反方理由；这不代表没有风险。"))
    lines.extend(
        [
            "",
            "## 数据缺口与下一动作",
            "",
            "| 优先级 | 缺口 | 原因 | 下一动作 |",
            "|---|---|---|---|",
        ]
    )
    gaps = report.get("data_gaps") or []
    if gaps:
        for gap in gaps:
            lines.append(
                f"| {_md(gap.get('severity_label'))} | {_md(gap.get('title'))} | "
                f"{_md(gap.get('reason'))} | {_md(gap.get('next_action'))} |"
            )
    else:
        lines.append("| -- | 当前门禁无结构化缺口 | 仍需完成人工运营核查 | 保留审慎复核 |")
    lines.extend(
        [
            "",
            "## 进入前人工运营核查",
            "",
            "| 项目 | 状态 | 说明 |",
            "|---|---|---|",
        ]
    )
    for item in report.get("operational_due_diligence") or []:
        lines.append(
            f"| {_md(item.get('label'))} | {_md(item.get('status_label'))} | {_md(item.get('detail'))} |"
        )
    assets = report.get("assets") or {}
    lines.extend(["", "## 关联商品", "", "| ASIN | 角色 | 价格 | 评论 | 近月购买量 | 时间点 | 成本 |", "|---|---|---:|---:|---:|---:|---|"])
    for row in assets.get("products") or []:
        lines.append(
            f"| {_md(row.get('asin'))} | {_md(row.get('role'))} | {_md(row.get('price'))} | "
            f"{_md(row.get('review_count'))} | {_md(row.get('monthly_bought'))} | "
            f"{_md(row.get('timepoint_count'))} | {'完整' if row.get('has_complete_unit_cost') else '未完整'} |"
        )
    if not assets.get("products"):
        lines.append("| -- | -- | -- | -- | -- | -- | -- |")
    lines.extend(["", "## 关联关键词", "", "| 关键词 | 角色 | 当前样本商品 | 排名时间点 | 最近采集 |", "|---|---|---:|---:|---|"])
    for row in assets.get("keywords") or []:
        lines.append(
            f"| {_md(row.get('keyword'))} | {_md(row.get('role'))} | "
            f"{_md(row.get('latest_batch_product_count'))} | {_md(row.get('timepoint_count'))} | "
            f"{_md(row.get('latest_snapshot_at'))} |"
        )
    if not assets.get("keywords"):
        lines.append("| -- | -- | -- | -- | -- |")
    lines.extend(
        [
            "",
            "## 方法与限制",
            "",
            report.get("guardrails", {}).get("statement") or "",
            "",
        ]
    )
    for item in report.get("guardrails", {}).get("prohibited_inferences") or []:
        lines.append(f"- {item}")
    lines.extend(["", f"_生成时间：{report.get('generated_at') or '--'}_", ""])
    return "\n".join(lines)


def parse_report_date(value: date | str | None) -> date:
    if value is None or value == "":
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ResearchDecisionReportError("评估日期格式应为 YYYY-MM-DD") from exc


def _fetch_report_supplement(cursor: Any, project_id: int) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
          p.id AS product_id,
          p.asin,
          snapshot_stats.timepoint_count,
          snapshot_stats.first_snapshot_at,
          snapshot_stats.latest_snapshot_at,
          (
            SELECT ps.id FROM product_snapshots ps
            WHERE ps.product_id = p.id
            ORDER BY ps.snapshot_at DESC, ps.id DESC LIMIT 1
          ) AS latest_product_snapshot_id,
          COALESCE(metric_stats.input_count, 0) AS metric_input_count,
          metric_stats.latest_metric_period_end,
          COALESCE(metric_stats.has_any_cost, 0) AS has_any_cost,
          COALESCE(metric_stats.has_complete_unit_cost, 0) AS has_complete_unit_cost,
          COALESCE(metric_stats.has_exact_conversion, 0) AS has_exact_conversion,
          COALESCE(metric_stats.has_official_input, 0) AS has_official_input
        FROM research_project_products rpp
        JOIN products p ON p.id = rpp.product_id
        LEFT JOIN (
          SELECT product_id,
                 COUNT(DISTINCT snapshot_at) AS timepoint_count,
                 MIN(snapshot_at) AS first_snapshot_at,
                 MAX(snapshot_at) AS latest_snapshot_at
          FROM product_snapshots
          GROUP BY product_id
        ) snapshot_stats ON snapshot_stats.product_id = p.id
        LEFT JOIN (
          SELECT product_id,
                 COUNT(*) AS input_count,
                 MAX(period_end) AS latest_metric_period_end,
                 MAX(CASE WHEN unit_purchase_cost IS NOT NULL
                               OR unit_shipping_cost IS NOT NULL
                               OR unit_fba_fee IS NOT NULL
                               OR unit_referral_fee IS NOT NULL
                               OR unit_other_cost IS NOT NULL THEN 1 ELSE 0 END) AS has_any_cost,
                 MAX(CASE WHEN unit_purchase_cost IS NOT NULL
                               AND unit_shipping_cost IS NOT NULL
                               AND unit_fba_fee IS NOT NULL
                               AND unit_referral_fee IS NOT NULL THEN 1 ELSE 0 END) AS has_complete_unit_cost,
                 MAX(CASE WHEN sessions IS NOT NULL
                               AND (units_ordered IS NOT NULL OR orders IS NOT NULL) THEN 1 ELSE 0 END)
                     AS has_exact_conversion,
                 MAX(CASE WHEN source_type IN ('sp_api', 'ads_api') THEN 1 ELSE 0 END)
                     AS has_official_input
          FROM product_metric_inputs
          GROUP BY product_id
        ) metric_stats ON metric_stats.product_id = p.id
        WHERE rpp.project_id = %s
        ORDER BY p.id
        """,
        (project_id,),
    )
    products = [_normalize_row(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT
          k.id AS keyword_id,
          k.keyword,
          rank_stats.timepoint_count,
          rank_stats.first_snapshot_at,
          rank_stats.latest_snapshot_at,
          latest_batch.latest_batch_product_count,
          serp_stats.serp_timepoint_count,
          serp_stats.latest_serp_at
        FROM research_project_keywords rpk
        JOIN keywords k ON k.id = rpk.keyword_id
        LEFT JOIN (
          SELECT keyword_id,
                 COUNT(DISTINCT snapshot_at) AS timepoint_count,
                 MIN(snapshot_at) AS first_snapshot_at,
                 MAX(snapshot_at) AS latest_snapshot_at
          FROM keyword_rank_snapshots
          GROUP BY keyword_id
        ) rank_stats ON rank_stats.keyword_id = k.id
        LEFT JOIN (
          SELECT ranks.keyword_id,
                 COUNT(DISTINCT ranks.product_id) AS latest_batch_product_count
          FROM keyword_rank_snapshots ranks
          JOIN (
            SELECT keyword_id, MAX(snapshot_at) AS latest_snapshot_at
            FROM keyword_rank_snapshots
            GROUP BY keyword_id
          ) latest
            ON latest.keyword_id = ranks.keyword_id
           AND latest.latest_snapshot_at = ranks.snapshot_at
          GROUP BY ranks.keyword_id
        ) latest_batch ON latest_batch.keyword_id = k.id
        LEFT JOIN (
          SELECT keyword_id,
                 COUNT(DISTINCT snapshot_at) AS serp_timepoint_count,
                 MAX(snapshot_at) AS latest_serp_at
          FROM keyword_serp_snapshots
          GROUP BY keyword_id
        ) serp_stats ON serp_stats.keyword_id = k.id
        WHERE rpk.project_id = %s
        ORDER BY k.id
        """,
        (project_id,),
    )
    keywords = [_normalize_row(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT
          krs.keyword_id,
          krs.snapshot_at,
          COUNT(*) AS row_count,
          COUNT(DISTINCT krs.product_id) AS product_count,
          SUM(CASE WHEN krs.is_sponsored = 0 THEN 1 ELSE 0 END) AS organic_row_count,
          COUNT(DISTINCT CASE
            WHEN krs.is_sponsored = 0 THEN krs.organic_rank
            ELSE NULL
          END) AS distinct_organic_rank_count,
          SUM(CASE
            WHEN krs.is_sponsored = 0
             AND (krs.organic_rank IS NULL OR krs.organic_rank < 1)
            THEN 1 ELSE 0
          END) AS invalid_organic_rank_count,
          COUNT(DISTINCT krs.page_no) AS page_count,
          MIN(CASE WHEN krs.is_sponsored = 0 THEN krs.organic_rank END) AS min_organic_rank,
          MAX(CASE WHEN krs.is_sponsored = 0 THEN krs.organic_rank END) AS max_organic_rank
        FROM research_project_keywords rpk
        JOIN keyword_rank_snapshots krs ON krs.keyword_id = rpk.keyword_id
        WHERE rpk.project_id = %s
        GROUP BY krs.keyword_id, krs.snapshot_at
        ORDER BY krs.keyword_id, krs.snapshot_at
        """,
        (project_id,),
    )
    rank_timepoints: dict[int, list[dict[str, Any]]] = {}
    for raw in cursor.fetchall():
        row = _normalize_row(raw)
        rank_timepoints.setdefault(int(row["keyword_id"]), []).append(row)
    for row in keywords:
        row["_rank_timepoints"] = rank_timepoints.get(int(row["keyword_id"]), [])

    cursor.execute(
        """
        SELECT
          mn.id AS niche_id,
          mn.marketplace,
          mn.name,
          mn.status,
          mn.definition,
          mn.category_scope,
          rpn.role,
          latest.id AS snapshot_id,
          latest.snapshot_at,
          latest.source_latest_at,
          latest.evidence_hash,
          latest.keyword_count,
          latest.keyword_with_rank_count,
          latest.keyword_with_serp_count,
          latest.rank_coverage,
          latest.serp_coverage,
          latest.serp_data_coverage,
          latest.page_count,
          latest.observed_product_count,
          latest.product_with_snapshot_count,
          latest.product_snapshot_coverage,
          latest.repeated_product_count,
          latest.cross_keyword_overlap,
          latest.manual_product_count,
          latest.price_p25,
          latest.price_median,
          latest.price_p75,
          latest.review_p25,
          latest.review_median,
          latest.review_p75,
          latest.rating_median,
          latest.monthly_bought_median,
          latest.monthly_bought_total,
          latest.monthly_bought_coverage,
          latest.demand_cr3,
          latest.demand_cr10,
          latest.ad_density,
          latest.brand_count,
          latest.brand_coverage,
          latest.brand_product_cr3,
          latest.raw_json,
          snapshot_stats.timepoint_count,
          snapshot_stats.first_snapshot_at,
          snapshot_stats.latest_snapshot_at
        FROM research_project_niches rpn
        JOIN market_niches mn ON mn.id = rpn.niche_id
        LEFT JOIN niche_snapshots latest
          ON latest.id = (
            SELECT ns.id FROM niche_snapshots ns
            WHERE ns.niche_id = mn.id
            ORDER BY ns.snapshot_at DESC, ns.id DESC LIMIT 1
          )
        LEFT JOIN (
          SELECT niche_id,
                 COUNT(*) AS timepoint_count,
                 MIN(snapshot_at) AS first_snapshot_at,
                 MAX(snapshot_at) AS latest_snapshot_at
          FROM niche_snapshots
          GROUP BY niche_id
        ) snapshot_stats ON snapshot_stats.niche_id = mn.id
        WHERE rpn.project_id = %s
        ORDER BY FIELD(rpn.role, 'primary', 'candidate', 'reference'), mn.id
        """,
        (project_id,),
    )
    niches = []
    for raw in cursor.fetchall():
        row = _normalize_row(raw)
        raw_json = row.pop("raw_json", None)
        parsed = _parse_json(raw_json)
        row["warnings"] = list(parsed.get("warnings") or [])
        timing = niche_snapshot_source_timing(parsed)
        row["rank_source_first_at"] = timing["first_at"]
        row["rank_source_latest_at"] = timing["latest_at"]
        row["rank_source_span_hours"] = timing["span_hours"]
        row["rank_source_span_days"] = timing["span_days"]
        row["rank_source_alignment"] = timing["alignment"]
        row["evidence_level"] = evidence_level(row if row.get("snapshot_id") else None)
        niches.append(row)
    return {"products": products, "keywords": keywords, "niches": niches}


def _build_gates(
    *,
    project: dict[str, Any],
    products: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    niches: list[dict[str, Any]],
    notes_by_type: dict[str, list[dict[str, Any]]],
    timeline: dict[str, Any],
    primary_niche: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    candidate_products = [row for row in products if row.get("role") == "candidate"]
    competition_products = [
        row for row in products if row.get("role") in {"benchmark", "competitor"}
    ]
    market_sample_ok = bool(
        primary_niche
        and int(primary_niche.get("observed_product_count") or 0) >= 20
        and (_number(primary_niche.get("rank_coverage")) or 0) >= 0.5
    )
    demand_ok = bool(
        market_sample_ok
        and (_number(primary_niche.get("monthly_bought_coverage")) or 0) >= 0.5
        and (_number(primary_niche.get("monthly_bought_total")) or 0) > 0
    )
    trend_strategy = "趋势" in str(project.get("strategy") or "") or "trend" in str(
        project.get("strategy") or ""
    ).lower()
    has_preliminary_trend = bool(timeline.get("preliminary_ready"))
    has_complete_cost = any(bool(row.get("has_complete_unit_cost")) for row in candidate_products)
    gates = [
        _gate(
            "objective",
            "研究目标明确",
            bool(str(project.get("objective") or "").strip()),
            True,
            "项目需要写明目标用户、价格带、场景或验证问题。",
            "在项目定义中补充可验证的研究目标。",
            "blocker",
        ),
        _gate(
            "candidate_product",
            "候选商品已关联",
            bool(candidate_products),
            True,
            f"当前候选商品 {len(candidate_products)} 个。",
            "从商品池关联至少一个真正准备评估的候选 ASIN。",
            "blocker",
        ),
        _gate(
            "competition_baseline",
            "竞争基线可用",
            bool(competition_products) or market_sample_ok,
            True,
            (
                f"直接对标/竞品 {len(competition_products)} 个；"
                f"市场去重样本 {int((primary_niche or {}).get('observed_product_count') or 0)} 个。"
            ),
            "关联对标/竞品，或先形成不少于 20 个去重 ASIN 的利基快照。",
            "blocker",
        ),
        _gate(
            "keyword_scope",
            "关键词边界已定义",
            bool(keywords),
            True,
            f"当前关联关键词 {len(keywords)} 个。",
            "关联种子词、核心词或长尾词，明确本项目观察范围。",
            "blocker",
        ),
        _gate(
            "market_snapshot",
            "市场证据快照已生成",
            bool(primary_niche and primary_niche.get("snapshot_id")),
            True,
            (
                f"当前关联利基 {len(niches)} 个；"
                f"主要快照 #{(primary_niche or {}).get('snapshot_id') or '--'}。"
            ),
            "在关联利基中补齐成员关键词并显式生成证据快照。",
            "blocker",
        ),
        _gate(
            "demand_coverage",
            "需求代理覆盖可用",
            demand_ok,
            True,
            (
                f"近月购买量覆盖 {_pct((primary_niche or {}).get('monthly_bought_coverage'))}；"
                f"去重样本 {int((primary_niche or {}).get('observed_product_count') or 0)} 个。"
            ),
            "补充代表性关键词批次；覆盖不足时只保留需求未知结论。",
            "high",
        ),
        _gate(
            "opportunity_hypothesis",
            "差异化假设已记录",
            bool(notes_by_type["opportunity"]),
            True,
            f"人工机会记录 {len(notes_by_type['opportunity'])} 条。",
            "记录目标用户痛点、规格改良或场景差异，并说明证据来源。",
            "high",
        ),
        _gate(
            "risk_record",
            "反方风险已记录",
            bool(notes_by_type["risk"]),
            True,
            f"人工风险记录 {len(notes_by_type['risk'])} 条。",
            "至少记录一条竞争、合规、供应链或履约方面的反方意见。",
            "high",
        ),
        _gate(
            "trend_evidence",
            "趋势观察达到最低条件",
            has_preliminary_trend,
            trend_strategy,
            (
                f"最强序列 {int(timeline.get('best_points') or 0)} 个合格时间点"
                f"（原始 {int(timeline.get('best_raw_points') or 0)} 个），"
                f"跨度 {int(timeline.get('best_span_days') or 0)} 个完整自然日。"
            ),
            f"使用追踪任务积累至少 {TREND_MIN_POINTS} 个合格时间点和 "
            f"{TREND_MIN_DAYS} 个完整自然日跨度。",
            "blocker" if trend_strategy else "medium",
        ),
        _gate(
            "financial_inputs",
            "候选商品成本输入完整",
            has_complete_cost,
            True,
            (
                f"候选商品 {len(candidate_products)} 个；"
                f"完整单件成本 {sum(bool(row.get('has_complete_unit_cost')) for row in candidate_products)} 个。"
            ),
            "产品进入候选后，人工录入采购、运输、FBA 和佣金等单件成本。",
            "high",
        ),
    ]
    return gates


def _build_readiness(
    project: dict[str, Any],
    gates: list[dict[str, Any]],
    products: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    niches: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = sum(bool(row["passed"]) for row in gates)
    blockers = [row for row in gates if row["critical_for_final_decision"] and not row["passed"]]
    has_assets = bool(products or keywords or niches)
    base_ready = all(
        next(row["passed"] for row in gates if row["key"] == key)
        for key in ("candidate_product", "keyword_scope", "market_snapshot")
    )
    if not has_assets:
        level = "not_started"
    elif not blockers and passed >= 8:
        level = "decision_ready"
    elif base_ready and passed >= 5:
        level = "reviewable"
    else:
        level = "evidence_building"
    recorded = str(project.get("status") or "") in {"approved", "rejected"}
    summary = {
        "not_started": "当前没有足够关联资产，报告只能列出研究准备项。",
        "evidence_building": "已有部分方向或资产，但关键证据尚未形成，不宜给出进入结论。",
        "reviewable": "已经可以组织人工复核，但仍有阻断最终进入判断的证据缺口。",
        "decision_ready": "结构化证据已达到人工决策门槛；仍需完成报告外的运营与合规核查。",
    }[level]
    if recorded and blockers:
        summary += " 项目已有人工作出终态结论，但当前报告仍检测到证据缺口，应复核原结论边界。"
    return {
        "level": level,
        "label": READINESS_LABELS[level],
        "summary": summary,
        "passed_count": passed,
        "total_count": len(gates),
        "blocking_count": len(blockers),
        "decision_recorded": recorded,
        "human_decision": project.get("decision_summary"),
        "gates": gates,
    }


def _build_axes(
    *,
    project: dict[str, Any],
    products: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    notes_by_type: dict[str, list[dict[str, Any]]],
    primary_niche: dict[str, Any] | None,
    timeline: dict[str, Any],
    freshness: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _demand_axis(primary_niche),
        _competition_axis(primary_niche),
        _differentiation_axis(project, products, notes_by_type),
        _trend_axis(timeline),
        _finance_axis(products),
        _evidence_axis(project, products, keywords, primary_niche, freshness),
    ]


def _demand_axis(niche: dict[str, Any] | None) -> dict[str, Any]:
    if not niche or not niche.get("snapshot_id"):
        return _axis(
            "demand",
            "需求证据",
            "insufficient",
            "low",
            "尚无可用利基快照，不能判断市场需求。",
            caveats=["没有用综合分或 BSR 替代需求证据。"],
        )
    observed = int(niche.get("observed_product_count") or 0)
    coverage = _number(niche.get("monthly_bought_coverage")) or 0
    demand_total = _number(niche.get("monthly_bought_total"))
    if observed >= 20 and coverage >= 0.5 and demand_total and demand_total > 0:
        status = "supporting"
        summary = "已观察到有覆盖基础的页面需求代理，但仍不是市场真实销量。"
    else:
        status = "mixed"
        summary = "已有市场观察样本，但需求代理覆盖或样本规模不足。"
    mixed_period = niche.get("rank_source_alignment") == "mixed_period"
    confidence = (
        "high"
        if niche.get("evidence_level") == "usable" and not mixed_period
        else "medium"
    )
    caveats = [
        "近月购买量是页面可见下界代理，不等于真实销量。",
        "当前样本只覆盖关联关键词已采集页面。",
    ]
    if mixed_period:
        caveats.append(
            f"成员关键词排名来源跨度 {_format_hours(niche.get('rank_source_span_hours'))}，"
            "需求分布是混合时点观察。"
        )
    return _axis(
        "demand",
        "需求证据",
        status,
        confidence,
        summary,
        facts=[
            _fact(
                "observed_products",
                "去重观察商品",
                observed,
                f"{observed:,} 个 ASIN",
                "niche_snapshot",
                niche,
            ),
            _fact(
                "monthly_bought_lower_bound_total",
                "近月购买量下界代理合计",
                demand_total,
                _fmt_number(demand_total),
                "niche_snapshot",
                niche,
            ),
            _fact(
                "monthly_bought_coverage",
                "需求代理覆盖",
                coverage,
                _pct(coverage),
                "niche_snapshot",
                niche,
            ),
            _fact(
                "demand_cr3",
                "需求前三集中度",
                niche.get("demand_cr3"),
                _pct(niche.get("demand_cr3")),
                "niche_snapshot",
                niche,
            ),
        ],
        caveats=caveats,
    )


def _competition_axis(niche: dict[str, Any] | None) -> dict[str, Any]:
    if not niche or not niche.get("snapshot_id"):
        return _axis(
            "competition",
            "竞争压力",
            "insufficient",
            "low",
            "没有市场快照，无法评估评论门槛、广告压力和头部集中。",
        )
    review_median = _number(niche.get("review_median"))
    ad_density = _number(niche.get("ad_density"))
    demand_cr3 = _number(niche.get("demand_cr3"))
    brand_cr3 = _number(niche.get("brand_product_cr3"))
    pressure: list[str] = []
    if review_median is not None and review_median >= 1000:
        pressure.append("评论中位数较高")
    if ad_density is not None and ad_density >= 0.35:
        pressure.append("广告卡片占比较高")
    if demand_cr3 is not None and demand_cr3 >= 0.65:
        pressure.append("需求代理向头部集中")
    if brand_cr3 is not None and brand_cr3 >= 0.60:
        pressure.append("已知品牌商品集中度较高")
    extreme = bool(
        (review_median is not None and review_median >= 5000)
        or (ad_density is not None and ad_density >= 0.5)
    )
    if extreme or len(pressure) >= 2:
        status = "risk"
        summary = "；".join(pressure) + "，直接同质化进入压力较大。"
    elif pressure:
        status = "mixed"
        summary = "；".join(pressure) + "，需要差异化和流量成本验证。"
    else:
        status = "supporting" if niche.get("evidence_level") == "usable" else "mixed"
        summary = (
            "已采集样本中未同时出现明显头部压力信号。"
            if status == "supporting"
            else "竞争指标存在，但证据覆盖不足以判断压力较低。"
        )
    mixed_period = niche.get("rank_source_alignment") == "mixed_period"
    caveats = [
        "广告密度只统计已采集结果卡片。",
        "品牌 CR3 是已知品牌商品占比，不是销售份额。",
    ]
    if mixed_period:
        caveats.append(
            f"成员关键词排名来源跨度 {_format_hours(niche.get('rank_source_span_hours'))}，"
            "跨词竞争结构不能当作严格同期截面。"
        )
    return _axis(
        "competition",
        "竞争压力",
        status,
        "high"
        if niche.get("evidence_level") == "usable" and not mixed_period
        else "medium",
        summary,
        facts=[
            _fact(
                "review_median",
                "评论数中位数",
                review_median,
                _fmt_number(review_median),
                "niche_snapshot",
                niche,
            ),
            _fact(
                "ad_density",
                "广告密度",
                ad_density,
                _pct(ad_density),
                "niche_snapshot",
                niche,
            ),
            _fact(
                "demand_cr3",
                "需求前三集中度",
                demand_cr3,
                _pct(demand_cr3),
                "niche_snapshot",
                niche,
            ),
            _fact(
                "brand_product_cr3",
                "品牌商品 CR3",
                brand_cr3,
                _pct(brand_cr3),
                "niche_snapshot",
                niche,
            ),
            _fact(
                "price_band",
                "价格带 P25 / 中位 / P75",
                niche.get("price_median"),
                f"${_fmt_number(niche.get('price_p25'))} / ${_fmt_number(niche.get('price_median'))} / ${_fmt_number(niche.get('price_p75'))}",
                "niche_snapshot",
                niche,
            ),
        ],
        caveats=caveats,
    )


def _differentiation_axis(
    project: dict[str, Any],
    products: list[dict[str, Any]],
    notes_by_type: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    opportunities = notes_by_type["opportunity"]
    benchmarks = [row for row in products if row.get("role") in {"benchmark", "competitor"}]
    candidates = [row for row in products if row.get("role") == "candidate"]
    detailed = [row for row in candidates if row.get("evidence_status") in {"partial", "collected"}]
    if opportunities and candidates and (benchmarks or detailed):
        status = "supporting"
        confidence = "medium"
        summary = "已有人工差异化假设，并存在候选与对标/详情证据可供核查。"
    elif opportunities or str(project.get("objective") or "").strip():
        status = "mixed"
        confidence = "low"
        summary = "已有方向描述，但差异化假设尚未与候选、对标或详情证据形成闭环。"
    else:
        status = "insufficient"
        confidence = "low"
        summary = "尚未记录可验证的用户痛点、规格改良或场景差异。"
    return _axis(
        "differentiation",
        "差异化假设",
        status,
        confidence,
        summary,
        facts=[
            _fact(
                "opportunity_note_count",
                "人工机会记录",
                len(opportunities),
                f"{len(opportunities)} 条",
                "manual_note",
            ),
            _fact(
                "candidate_product_count",
                "候选商品",
                len(candidates),
                f"{len(candidates)} 个",
                "project_relation",
            ),
            _fact(
                "benchmark_product_count",
                "直接对标/竞品",
                len(benchmarks),
                f"{len(benchmarks)} 个",
                "project_relation",
            ),
            _fact(
                "candidate_detail_count",
                "候选详情证据",
                len(detailed),
                f"{len(detailed)} 个",
                "product_detail",
            ),
        ],
        caveats=["系统不从标题或评分自动推断差异化，最终假设必须由人工确认。"],
    )


def _trend_axis(timeline: dict[str, Any]) -> dict[str, Any]:
    points = int(timeline.get("best_points") or 0)
    raw_points = int(timeline.get("best_raw_points") or points)
    span = int(timeline.get("best_span_days") or 0)
    if timeline.get("stable_ready"):
        status = "supporting"
        confidence = "medium"
        summary = "时间点和跨度达到较稳定的短期观察条件，但仍不足以判断季节性。"
    elif timeline.get("preliminary_ready"):
        status = "mixed"
        confidence = "low"
        summary = "达到初步趋势观察条件，只能描述短期变化，不能外推长期增长。"
    elif timeline.get("context_only_ready"):
        status = "insufficient"
        confidence = "low"
        summary = "候选、核心词或主利基的时间证据不足；对标/参考资产虽有更长历史，但只能作为旁证。"
    else:
        status = "insufficient"
        confidence = "low"
        summary = "时间点或跨度不足，当前不能形成趋势判断。"
    caveats = [
        f"初步趋势至少需要 {TREND_MIN_POINTS} 个合格时间点且跨度 {TREND_MIN_DAYS} 个完整自然日。",
        "趋势门禁只认可候选商品、核心/种子关键词和主利基；对标或参考资产不能代替目标序列。",
        "季节性需要跨周期数据，V1 不作判断。",
    ]
    caveats.extend(str(item) for item in timeline.get("best_quality_warnings") or [])
    if timeline.get("context_only_ready"):
        caveats.append(
            "当前最长旁证为"
            f"{timeline.get('context_best_source_label') or '参考资产'}"
            f" {int(timeline.get('context_best_points') or 0)} 点、"
            f"{int(timeline.get('context_best_span_days') or 0)} 天。"
        )
    return _axis(
        "trend",
        "趋势与持续性",
        status,
        confidence,
        summary,
        facts=[
            _fact(
                "best_timepoint_count",
                "合格时间点",
                points,
                f"{points} 个时间点",
                "time_series",
            ),
            _fact(
                "best_raw_timepoint_count",
                "原始时间点",
                raw_points,
                f"{raw_points} 个时间点",
                "time_series",
            ),
            _fact(
                "best_span_days",
                "最大观察跨度",
                span,
                f"{span} 天",
                "time_series",
            ),
            _fact(
                "best_source",
                "序列来源",
                timeline.get("best_source"),
                timeline.get("best_source_label") or "--",
                "time_series",
            ),
        ],
        caveats=caveats,
    )


def _finance_axis(products: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in products if row.get("role") == "candidate"]
    complete = [row for row in candidates if row.get("has_complete_unit_cost")]
    any_cost = [row for row in candidates if row.get("has_any_cost")]
    official = [row for row in candidates if row.get("has_official_input")]
    if complete:
        status = "supporting"
        confidence = "high" if official else "medium"
        summary = "至少一个候选商品已有完整单件成本，可进入人工利润情景核算。"
    elif any_cost:
        status = "mixed"
        confidence = "low"
        summary = "已有部分成本，但不足以判断贡献利润、盈亏平衡和最大广告承受能力。"
    else:
        status = "insufficient"
        confidence = "low"
        summary = "没有候选商品的完整成本输入，不能判断财务可行性。"
    return _axis(
        "finance",
        "财务可行性",
        status,
        confidence,
        summary,
        facts=[
            _fact(
                "candidate_product_count",
                "候选商品",
                len(candidates),
                f"{len(candidates)} 个",
                "project_relation",
            ),
            _fact(
                "complete_unit_cost_count",
                "完整单件成本",
                len(complete),
                f"{len(complete)} 个",
                "manual_or_official_input",
            ),
            _fact(
                "partial_cost_count",
                "部分成本",
                len(any_cost),
                f"{len(any_cost)} 个",
                "manual_or_official_input",
            ),
            _fact(
                "official_input_count",
                "官方经营输入",
                len(official),
                f"{len(official)} 个",
                "official_input",
            ),
        ],
        caveats=["经验转化率和前台需求代理不能代替真实采购、履约、广告和退货成本。"],
    )


def _evidence_axis(
    project: dict[str, Any],
    products: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    niche: dict[str, Any] | None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    coverage = _number(project.get("evidence_coverage")) or 0
    freshness_status = freshness.get("status")
    mixed_period = bool(niche and niche.get("rank_source_alignment") == "mixed_period")
    if (
        coverage >= 75
        and niche
        and niche.get("evidence_level") == "usable"
        and freshness_status == "current"
        and not mixed_period
    ):
        status, confidence = "supporting", "high"
        summary = "关联资产覆盖、利基证据和来源时效均较完整。"
    elif products or keywords or niche:
        status = "risk" if freshness_status == "stale" else "mixed"
        confidence = "medium" if status == "mixed" else "low"
        summary = (
            "已有事实来源，但成员关键词来自相隔较远的批次，当前只能作为混合时点证据。"
            if mixed_period
            else "已有事实来源，但字段覆盖、利基样本或时效仍有缺口。"
        )
    else:
        status, confidence = "insufficient", "low"
        summary = "尚无足够来源形成可审计报告。"
    caveats = ["字段存在不等于结论成立；来源与时间口径仍需逐项核验。"]
    if mixed_period:
        caveats.append(
            f"关键词排名批次跨度 {_format_hours(niche.get('rank_source_span_hours'))}，"
            "建议统一窗口补采后再比较跨词结构。"
        )
    return _axis(
        "evidence_quality",
        "证据质量",
        status,
        confidence,
        summary,
        facts=[
            _fact(
                "project_evidence_coverage",
                "项目字段覆盖",
                coverage,
                f"{coverage:.0f}%",
                "project_summary",
            ),
            _fact(
                "product_count",
                "商品数量",
                len(products),
                f"{len(products)} 个",
                "project_relation",
            ),
            _fact(
                "keyword_count",
                "关键词数量",
                len(keywords),
                f"{len(keywords)} 个",
                "project_relation",
            ),
            _fact(
                "freshness_status",
                "来源新鲜度",
                freshness_status,
                freshness.get("label") or "--",
                "source_manifest",
            ),
            _fact(
                "rank_source_span_hours",
                "关键词排名批次跨度",
                niche.get("rank_source_span_hours") if niche else None,
                _format_hours(niche.get("rank_source_span_hours")) if niche else "--",
                "niche_snapshot",
                niche,
            ),
        ],
        caveats=caveats,
    )


def _build_arguments(
    axes: list[dict[str, Any]],
    notes_by_type: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    supporting: list[dict[str, Any]] = []
    counter: list[dict[str, Any]] = []
    for axis in axes:
        if axis["key"] == "evidence_quality":
            continue
        item = {
            "title": axis["label"],
            "detail": axis["summary"],
            "strength": axis["confidence"],
            "strength_label": axis["confidence_label"],
            "source": "结构化证据",
            "axis": axis["key"],
        }
        if axis["status"] == "supporting":
            supporting.append(item)
        elif axis["status"] in {"risk", "mixed"}:
            counter.append(item)
    for note in notes_by_type["opportunity"]:
        supporting.append(
            {
                "title": "人工机会判断",
                "detail": _excerpt(note.get("content")),
                "strength": "manual",
                "strength_label": "待事实复核",
                "source": f"项目记录 #{note.get('id')}",
                "axis": "differentiation",
            }
        )
    for note in notes_by_type["risk"]:
        counter.append(
            {
                "title": "人工风险判断",
                "detail": _excerpt(note.get("content")),
                "strength": "manual",
                "strength_label": "人工记录",
                "source": f"项目记录 #{note.get('id')}",
                "axis": "manual_risk",
            }
        )
    return {"supporting": supporting[:12], "counter": counter[:12]}


def _build_data_gaps(
    *,
    project: dict[str, Any],
    gates: list[dict[str, Any]],
    freshness: dict[str, Any],
    products: list[dict[str, Any]],
    primary_niche: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    final_stage = str(project.get("status") or "") in FINAL_STAGE_STATUSES
    route_by_gate = {
        "objective": f"#/research-projects/{project.get('id')}",
        "candidate_product": "#/products",
        "competition_baseline": "#/products",
        "keyword_scope": "#/keyword-library",
        "market_snapshot": (
            f"#/market-niches/{primary_niche.get('niche_id')}"
            if primary_niche
            else "#/market-niches"
        ),
        "demand_coverage": (
            f"#/market-niches/{primary_niche.get('niche_id')}"
            if primary_niche
            else "#/market-niches"
        ),
        "opportunity_hypothesis": f"#/research-projects/{project.get('id')}",
        "risk_record": f"#/research-projects/{project.get('id')}",
        "trend_evidence": "#/tracking",
        "financial_inputs": (
            f"#/metrics/{next((row.get('asin') for row in products if row.get('role') == 'candidate'), '')}"
            if any(row.get("role") == "candidate" for row in products)
            else "#/metrics"
        ),
    }
    gaps = []
    for gate in gates:
        if gate["passed"]:
            continue
        severity = gate["severity"]
        if gate["key"] == "financial_inputs" and final_stage:
            severity = "blocker"
        gaps.append(
            {
                "key": gate["key"],
                "category": "decision_gate",
                "severity": severity,
                "severity_label": SEVERITY_LABELS[severity],
                "title": gate["label"],
                "reason": gate["detail"],
                "next_action": gate["action"],
                "route": route_by_gate.get(gate["key"]),
                "requires_user_action": True,
                "automatic_action": False,
            }
        )
    if freshness.get("status") == "stale":
        gaps.append(
            {
                "key": "stale_sources",
                "category": "source_quality",
                "severity": "high",
                "severity_label": SEVERITY_LABELS["high"],
                "title": "主要证据已过期",
                "reason": f"评估日距离最近来源约 {freshness.get('latest_age_days')} 天。",
                "next_action": "由用户显式刷新相关关键词或商品证据，再重新生成报告。",
                "route": "#/tasks",
                "requires_user_action": True,
                "automatic_action": False,
            }
        )
    order = {"blocker": 0, "high": 1, "medium": 2, "low": 3}
    return sorted(gaps, key=lambda row: (order[row["severity"]], row["title"]))


def _build_due_diligence(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in products if row.get("role") == "candidate"]
    complete_cost = any(row.get("has_complete_unit_cost") for row in candidates)
    return [
        _due_item(
            "landed_cost",
            "落地成本与 FBA 尺寸分段",
            "partial" if complete_cost else "not_verified",
            "已有完整单件成本输入，仍需核对仓储和退货情景。"
            if complete_cost
            else "需要供应商报价、头程、FBA、佣金和其他单件成本。",
        ),
        _due_item("ip_compliance", "知识产权与合规", "not_verified", "商标、专利、版权、认证和禁限售需人工核验。"),
        _due_item("supplier", "供应链能力", "not_verified", "MOQ、交期、良率、补货稳定性和付款条件尚未结构化核验。"),
        _due_item("packaging_returns", "包装与退货风险", "not_verified", "破损、尺寸、重量、误用和退货原因需要样品与运营数据。"),
        _due_item("launch_budget", "上市与广告预算", "not_verified", "关键词竞价、首批库存和冷启动预算需要卖家侧真实数据。"),
        _due_item("seasonality", "季节性与库存周期", "not_verified", "现有时间序列不足以判断季节性和备货峰值。"),
        _due_item("account_constraints", "类目与账号门槛", "not_verified", "类目审核、品牌备案和目标账号权限需在 Seller Central 核验。"),
    ]


def _build_freshness(
    products: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    niches: list[dict[str, Any]],
    evaluated_on: date,
) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    for row in products:
        _append_source_date(sources, "product_snapshot", row.get("asin"), row.get("latest_snapshot_at"))
    for row in keywords:
        _append_source_date(sources, "keyword_rank", row.get("keyword"), row.get("latest_snapshot_at"))
    for row in niches:
        _append_source_date(sources, "niche_snapshot", row.get("name"), row.get("snapshot_at"))
    if not sources:
        return {
            "status": "missing",
            "label": "无可用时间来源",
            "latest_source_at": None,
            "oldest_source_at": None,
            "latest_age_days": None,
            "source_count": 0,
            "stale_source_count": 0,
        }
    for row in sources:
        row["age_days"] = max(0, (evaluated_on - row["_date"]).days)
        row.pop("_date", None)
    ages = [int(row["age_days"]) for row in sources]
    stale_count = sum(age > 30 for age in ages)
    aging_count = sum(7 < age <= 30 for age in ages)
    latest = max(sources, key=lambda row: str(row["observed_at"]))
    oldest = min(sources, key=lambda row: str(row["observed_at"]))
    if stale_count and stale_count >= max(1, len(sources) / 2):
        status, label = "stale", "主要来源超过 30 天"
    elif stale_count or aging_count:
        status, label = "mixed", "来源时效不一致"
    else:
        status, label = "current", "主要来源在 7 天内"
    return {
        "status": status,
        "label": label,
        "latest_source_at": latest["observed_at"],
        "oldest_source_at": oldest["observed_at"],
        "latest_age_days": min(ages),
        "oldest_age_days": max(ages),
        "source_count": len(sources),
        "stale_source_count": stale_count,
        "sources": sources,
    }


def _normalize_keywords(
    base_rows: Iterable[dict[str, Any]],
    supplement_rows: Iterable[dict[str, Any]],
    *,
    evaluated_on: date,
) -> list[dict[str, Any]]:
    merged = _merge_rows(base_rows, supplement_rows, key="keyword_id")
    for row in merged:
        timepoints = row.pop("_rank_timepoints", None)
        quality = qualify_keyword_rank_timepoints(
            timepoints or [],
            raw_timepoint_count=int(row.get("timepoint_count") or 0),
            raw_first_snapshot_at=row.get("first_snapshot_at"),
            raw_latest_snapshot_at=row.get("latest_snapshot_at"),
            evaluated_on=evaluated_on,
        )
        row.update(quality)
    return merged


def qualify_keyword_rank_timepoints(
    timepoints: Iterable[dict[str, Any]],
    *,
    raw_timepoint_count: int = 0,
    raw_first_snapshot_at: Any = None,
    raw_latest_snapshot_at: Any = None,
    evaluated_on: date | str | None = None,
) -> dict[str, Any]:
    """Qualify keyword ranking snapshots without changing source records."""

    evaluated_date = parse_report_date(evaluated_on)
    normalized: list[dict[str, Any]] = []
    for raw in timepoints:
        observed_at = _parse_datetime(raw.get("snapshot_at"))
        if observed_at is None:
            continue
        row_count = max(0, int(raw.get("row_count") or 0))
        product_count = max(0, int(raw.get("product_count") or 0))
        organic_rows = max(0, int(raw.get("organic_row_count") or 0))
        distinct_ranks = max(0, int(raw.get("distinct_organic_rank_count") or 0))
        invalid_ranks = max(0, int(raw.get("invalid_organic_rank_count") or 0))
        duplicate_products = max(0, row_count - product_count)
        rank_integrity = (
            organic_rows > 0
            and distinct_ranks == organic_rows
            and invalid_ranks == 0
            and duplicate_products == 0
        )
        normalized.append(
            {
                "snapshot_at": observed_at,
                "row_count": row_count,
                "product_count": product_count,
                "organic_row_count": organic_rows,
                "distinct_organic_rank_count": distinct_ranks,
                "invalid_organic_rank_count": invalid_ranks,
                "duplicate_product_count": duplicate_products,
                "page_count": max(0, int(raw.get("page_count") or 0)),
                "min_organic_rank": raw.get("min_organic_rank"),
                "max_organic_rank": raw.get("max_organic_rank"),
                "rank_integrity": rank_integrity,
            }
        )
    normalized.sort(key=lambda row: row["snapshot_at"])
    raw_count = max(int(raw_timepoint_count or 0), len(normalized))
    raw_first = _parse_datetime(raw_first_snapshot_at)
    raw_latest = _parse_datetime(raw_latest_snapshot_at)
    if normalized:
        raw_first = raw_first or normalized[0]["snapshot_at"]
        raw_latest = raw_latest or normalized[-1]["snapshot_at"]

    if not normalized and raw_count > 0:
        freshness = _timeline_freshness(raw_latest, evaluated_on=evaluated_date)
        return {
            "raw_timepoint_count": raw_count,
            "raw_independent_window_count": raw_count,
            "qualified_timepoint_count": raw_count,
            "rank_integrity_timepoint_count": raw_count,
            "invalid_rank_timepoint_count": 0,
            "near_duplicate_timepoint_count": 0,
            "excluded_timepoint_count": 0,
            "raw_first_snapshot_at": _format_datetime_value(raw_first),
            "raw_latest_snapshot_at": _format_datetime_value(raw_latest),
            "qualified_first_snapshot_at": _format_datetime_value(raw_first),
            "qualified_latest_snapshot_at": _format_datetime_value(raw_latest),
            "qualified_span_days": _span_days(raw_first, raw_latest),
            "timepoint_quality_status": "aggregate_only",
            "timepoint_quality_label": "聚合兼容",
            "timepoint_quality_warnings": [
                "当前仅有聚合时间点，缺少逐批排名完整性明细；按兼容口径展示。"
            ],
            **freshness,
        }

    raw_windows = _select_independent_timepoints(normalized)
    integrity_points = [row for row in normalized if row["rank_integrity"]]
    qualified = _select_independent_timepoints(integrity_points)
    invalid_count = sum(1 for row in normalized if not row["rank_integrity"])
    near_duplicate_count = max(0, len(normalized) - len(raw_windows))
    qualified_count = len(qualified)
    uninspected_count = max(0, raw_count - len(normalized))
    excluded_count = max(0, raw_count - qualified_count)
    qualified_first = qualified[0]["snapshot_at"] if qualified else None
    qualified_latest = qualified[-1]["snapshot_at"] if qualified else None
    freshness = _timeline_freshness(
        qualified_latest or raw_latest,
        evaluated_on=evaluated_date,
    )
    warnings: list[str] = []
    if invalid_count:
        warnings.append(
            f"{invalid_count} 个批次存在跨页自然位次重复或缺失，仅保留为商品出现范围旁证。"
        )
    if near_duplicate_count:
        warnings.append(
            f"{near_duplicate_count} 个时间点与相邻观察不足 "
            f"{TREND_INDEPENDENT_WINDOW_HOURS} 小时，不单独计入趋势窗口。"
        )
    if uninspected_count:
        warnings.append(f"{uninspected_count} 个聚合时间点缺少批次质量明细，未计入合格趋势。")
    if freshness["timepoint_freshness_status"] == "stale":
        warnings.append("最近合格点已超过 7 天，趋势可作历史旁证但需要更新。")
    elif freshness["timepoint_freshness_status"] == "historical":
        warnings.append("最近合格点已超过 30 天，只能作为历史证据。")
    if qualified_count == 0:
        status, label = "invalid", "仅出现范围"
    elif excluded_count:
        status, label = "partial", "部分可用"
    else:
        status, label = "qualified", "排名完整"
    return {
        "raw_timepoint_count": raw_count,
        "raw_independent_window_count": len(raw_windows),
        "qualified_timepoint_count": qualified_count,
        "rank_integrity_timepoint_count": len(integrity_points),
        "invalid_rank_timepoint_count": invalid_count,
        "near_duplicate_timepoint_count": near_duplicate_count,
        "excluded_timepoint_count": excluded_count,
        "raw_first_snapshot_at": _format_datetime_value(raw_first),
        "raw_latest_snapshot_at": _format_datetime_value(raw_latest),
        "qualified_first_snapshot_at": _format_datetime_value(qualified_first),
        "qualified_latest_snapshot_at": _format_datetime_value(qualified_latest),
        "qualified_span_days": _span_days(qualified_first, qualified_latest),
        "timepoint_quality_status": status,
        "timepoint_quality_label": label,
        "timepoint_quality_warnings": warnings,
        **freshness,
    }


def _select_independent_timepoints(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    minimum_gap = timedelta(hours=TREND_INDEPENDENT_WINDOW_HOURS)
    for row in sorted(rows, key=lambda item: item["snapshot_at"]):
        if not selected or row["snapshot_at"] - selected[-1]["snapshot_at"] >= minimum_gap:
            selected.append(row)
            continue
        current = selected[-1]
        current_key = (
            int(bool(current.get("rank_integrity"))),
            int(current.get("product_count") or 0),
            current["snapshot_at"],
        )
        candidate_key = (
            int(bool(row.get("rank_integrity"))),
            int(row.get("product_count") or 0),
            row["snapshot_at"],
        )
        if candidate_key > current_key:
            selected[-1] = row
    return selected


def select_independent_keyword_timepoints(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose the report's 24-hour independence rule to read-only evidence views."""

    return _select_independent_timepoints(rows)


def _timeline_freshness(observed_at: datetime | None, *, evaluated_on: date) -> dict[str, Any]:
    if observed_at is None:
        return {
            "timepoint_latest_age_days": None,
            "timepoint_freshness_status": "missing",
            "timepoint_freshness_label": "无合格时间点",
        }
    age_days = max(0, (evaluated_on - observed_at.date()).days)
    if age_days <= 7:
        status, label = "current", "当前（7天内）"
    elif age_days <= 30:
        status, label = "stale", "需更新（8-30天）"
    else:
        status, label = "historical", "历史证据（超过30天）"
    return {
        "timepoint_latest_age_days": age_days,
        "timepoint_freshness_status": status,
        "timepoint_freshness_label": label,
    }


def _format_datetime_value(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value is not None else None


def _build_timeline_summary(
    products: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    niches: list[dict[str, Any]],
    *,
    evaluated_on: date,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for source, label, rows in (
        ("product", "商品快照", products),
        ("keyword", "关键词排名", keywords),
        ("niche", "利基快照", niches),
    ):
        for row in rows:
            raw_points = int(
                (
                    row.get("raw_timepoint_count")
                    if source == "keyword"
                    else row.get("timepoint_count")
                )
                or 0
            )
            if source == "keyword":
                points = int(row.get("qualified_timepoint_count") or 0)
                first_snapshot_at = row.get("qualified_first_snapshot_at")
                latest_snapshot_at = row.get("qualified_latest_snapshot_at")
                span = int(row.get("qualified_span_days") or 0)
                quality_status = row.get("timepoint_quality_status")
                quality_label = row.get("timepoint_quality_label")
                quality_warnings = list(row.get("timepoint_quality_warnings") or [])
                invalid_rank_points = int(row.get("invalid_rank_timepoint_count") or 0)
                near_duplicate_points = int(row.get("near_duplicate_timepoint_count") or 0)
                freshness_status = row.get("timepoint_freshness_status")
                freshness_label = row.get("timepoint_freshness_label")
                latest_age_days = row.get("timepoint_latest_age_days")
            else:
                points = raw_points
                first_snapshot_at = row.get("first_snapshot_at")
                latest_snapshot_at = row.get("latest_snapshot_at")
                span = _span_days(first_snapshot_at, latest_snapshot_at)
                quality_status = "direct"
                quality_label = "直接时间点"
                quality_warnings = []
                invalid_rank_points = 0
                near_duplicate_points = 0
                freshness = _timeline_freshness(
                    _parse_datetime(latest_snapshot_at),
                    evaluated_on=evaluated_on,
                )
                freshness_status = freshness["timepoint_freshness_status"]
                freshness_label = freshness["timepoint_freshness_label"]
                latest_age_days = freshness["timepoint_latest_age_days"]
            candidates.append(
                {
                    "source": source,
                    "source_label": label,
                    "name": row.get("asin") or row.get("keyword") or row.get("name"),
                    "points": points,
                    "raw_points": raw_points,
                    "span_days": span,
                    "first_snapshot_at": first_snapshot_at,
                    "latest_snapshot_at": latest_snapshot_at,
                    "role": row.get("role"),
                    "decision_relevant": _timeline_role_is_relevant(source, row.get("role")),
                    "excluded_points": max(0, raw_points - points),
                    "invalid_rank_points": invalid_rank_points,
                    "near_duplicate_points": near_duplicate_points,
                    "quality_status": quality_status,
                    "quality_label": quality_label,
                    "quality_warnings": quality_warnings,
                    "freshness_status": freshness_status,
                    "freshness_label": freshness_label,
                    "latest_age_days": latest_age_days,
                }
            )
    usable_candidates = [row for row in candidates if row["points"] > 0 or row["raw_points"] > 0]
    decision_candidates = [row for row in usable_candidates if row["decision_relevant"]]
    best = max(
        decision_candidates,
        key=lambda row: (row["span_days"], row["points"]),
        default=None,
    )
    context_best = max(
        usable_candidates,
        key=lambda row: (row["span_days"], row["points"]),
        default=None,
    )
    points = int((best or {}).get("points") or 0)
    raw_points = int((best or {}).get("raw_points") or 0)
    span = int((best or {}).get("span_days") or 0)
    context_points = int((context_best or {}).get("points") or 0)
    context_span = int((context_best or {}).get("span_days") or 0)
    preliminary_ready = points >= TREND_MIN_POINTS and span >= TREND_MIN_DAYS
    context_ready = context_points >= TREND_MIN_POINTS and context_span >= TREND_MIN_DAYS
    return {
        "best_source": (best or {}).get("source"),
        "best_source_label": (best or {}).get("source_label"),
        "best_name": (best or {}).get("name"),
        "best_points": points,
        "best_raw_points": raw_points,
        "best_excluded_points": int((best or {}).get("excluded_points") or 0),
        "best_invalid_rank_points": int((best or {}).get("invalid_rank_points") or 0),
        "best_near_duplicate_points": int((best or {}).get("near_duplicate_points") or 0),
        "best_span_days": span,
        "best_first_snapshot_at": (best or {}).get("first_snapshot_at"),
        "best_latest_snapshot_at": (best or {}).get("latest_snapshot_at"),
        "best_quality_status": (best or {}).get("quality_status"),
        "best_quality_label": (best or {}).get("quality_label"),
        "best_quality_warnings": list((best or {}).get("quality_warnings") or []),
        "best_freshness_status": (best or {}).get("freshness_status"),
        "best_freshness_label": (best or {}).get("freshness_label"),
        "best_latest_age_days": (best or {}).get("latest_age_days"),
        "preliminary_ready": preliminary_ready,
        "stable_ready": points >= TREND_STABLE_POINTS and span >= TREND_STABLE_DAYS,
        "context_best_source": (context_best or {}).get("source"),
        "context_best_source_label": (context_best or {}).get("source_label"),
        "context_best_name": (context_best or {}).get("name"),
        "context_best_role": (context_best or {}).get("role"),
        "context_best_points": context_points,
        "context_best_span_days": context_span,
        "context_only_ready": context_ready and not preliminary_ready,
        "seasonality_ready": False,
        "minimum_independent_window_hours": TREND_INDEPENDENT_WINDOW_HOURS,
        "rank_integrity_required": True,
        "sequences": [
            {
                key: row.get(key)
                for key in (
                    "source",
                    "source_label",
                    "name",
                    "role",
                    "points",
                    "raw_points",
                    "span_days",
                    "first_snapshot_at",
                    "latest_snapshot_at",
                    "excluded_points",
                    "invalid_rank_points",
                    "near_duplicate_points",
                    "quality_status",
                    "quality_label",
                    "quality_warnings",
                    "freshness_status",
                    "freshness_label",
                    "latest_age_days",
                )
            }
            for row in sorted(
                decision_candidates,
                key=lambda item: (item["span_days"], item["points"], item["raw_points"]),
                reverse=True,
            )
        ],
    }


def _timeline_role_is_relevant(source: str, role: Any) -> bool:
    role_value = str(role or "").strip().lower()
    if source == "product":
        return role_value == "candidate"
    if source == "keyword":
        return role_value in {"seed", "core"}
    if source == "niche":
        return role_value == "primary"
    return False


def _normalize_niches(
    base_rows: Iterable[dict[str, Any]],
    supplement_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = _merge_rows(base_rows, supplement_rows, key="niche_id")
    for row in merged:
        row.setdefault("evidence_level", evidence_level(row if row.get("snapshot_id") else None))
    return merged


def _select_primary_niche(niches: list[dict[str, Any]]) -> dict[str, Any] | None:
    role_order = {"primary": 0, "candidate": 1, "reference": 2}
    evidence_order = {"usable": 0, "partial": 1, "insufficient": 2, "not_generated": 3}
    return min(
        niches,
        key=lambda row: (
            role_order.get(str(row.get("role")), 9),
            evidence_order.get(str(row.get("evidence_level")), 9),
            -int(row.get("observed_product_count") or 0),
        ),
        default=None,
    )


def _build_source_manifest(
    *,
    project: dict[str, Any],
    products: list[dict[str, Any]],
    keywords: list[dict[str, Any]],
    niches: list[dict[str, Any]],
    notes: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "project": {
            key: project.get(key)
            for key in (
                "id",
                "marketplace",
                "name",
                "status",
                "objective",
                "strategy",
                "decision_summary",
                "status_changed_at",
                "updated_at",
            )
        },
        "products": [
            {
                key: row.get(key)
                for key in (
                    "product_id",
                    "asin",
                    "role",
                    "notes",
                    "category_path",
                    "date_first_available",
                    "detail_collected_at",
                    "latest_product_snapshot_id",
                    "latest_snapshot_at",
                    "price",
                    "rating",
                    "review_count",
                    "monthly_bought",
                    "timepoint_count",
                    "first_snapshot_at",
                    "metric_input_count",
                    "latest_metric_period_end",
                    "has_any_cost",
                    "has_complete_unit_cost",
                    "has_exact_conversion",
                    "has_official_input",
                )
            }
            for row in products
        ],
        "keywords": [
            {
                key: row.get(key)
                for key in (
                    "keyword_id",
                    "keyword",
                    "role",
                    "notes",
                    "timepoint_count",
                    "raw_timepoint_count",
                    "raw_independent_window_count",
                    "qualified_timepoint_count",
                    "rank_integrity_timepoint_count",
                    "invalid_rank_timepoint_count",
                    "near_duplicate_timepoint_count",
                    "excluded_timepoint_count",
                    "first_snapshot_at",
                    "latest_snapshot_at",
                    "qualified_first_snapshot_at",
                    "qualified_latest_snapshot_at",
                    "qualified_span_days",
                    "timepoint_quality_status",
                    "timepoint_freshness_status",
                    "latest_batch_product_count",
                    "serp_timepoint_count",
                    "latest_serp_at",
                )
            }
            for row in keywords
        ],
        "niches": [
            {
                key: row.get(key)
                for key in (
                    "niche_id",
                    "name",
                    "role",
                    "snapshot_id",
                    "snapshot_at",
                    "rank_source_first_at",
                    "rank_source_latest_at",
                    "rank_source_span_hours",
                    "rank_source_alignment",
                    "evidence_hash",
                    "evidence_level",
                    "observed_product_count",
                    "rank_coverage",
                    "serp_coverage",
                    "product_snapshot_coverage",
                    "monthly_bought_total",
                    "monthly_bought_coverage",
                    "demand_cr3",
                    "ad_density",
                    "review_median",
                    "brand_product_cr3",
                    "timepoint_count",
                    "first_snapshot_at",
                    "latest_snapshot_at",
                )
            }
            for row in niches
        ],
        "notes": [
            {
                "id": row.get("id"),
                "note_type": row.get("note_type"),
                "content_hash": hashlib.sha256(
                    " ".join(str(row.get("content") or "").split()).casefold().encode("utf-8")
                ).hexdigest(),
                "updated_at": row.get("updated_at"),
            }
            for row in notes
        ],
    }


def _project_summary(project: dict[str, Any]) -> dict[str, Any]:
    return {
        key: project.get(key)
        for key in (
            "id",
            "marketplace",
            "name",
            "status",
            "status_label",
            "objective",
            "strategy",
            "decision_summary",
            "decided_at",
            "status_changed_at",
            "created_at",
            "updated_at",
        )
    }


def _compact_product(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "product_id",
            "asin",
            "title",
            "title_zh",
            "role",
            "brand",
            "category_path",
            "price",
            "rating",
            "review_count",
            "monthly_bought",
            "snapshot_at",
            "latest_snapshot_at",
            "timepoint_count",
            "first_snapshot_at",
            "evidence_status",
            "evidence_collected",
            "evidence_total",
            "metric_input_count",
            "latest_metric_period_end",
            "has_any_cost",
            "has_complete_unit_cost",
            "has_exact_conversion",
            "has_official_input",
        )
    }


def _compact_keyword(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "keyword_id",
            "keyword",
            "role",
            "product_count",
            "latest_batch_product_count",
            "rank_snapshot_count",
            "timepoint_count",
            "raw_timepoint_count",
            "raw_independent_window_count",
            "qualified_timepoint_count",
            "rank_integrity_timepoint_count",
            "invalid_rank_timepoint_count",
            "near_duplicate_timepoint_count",
            "excluded_timepoint_count",
            "first_snapshot_at",
            "latest_snapshot_at",
            "qualified_first_snapshot_at",
            "qualified_latest_snapshot_at",
            "qualified_span_days",
            "timepoint_quality_status",
            "timepoint_quality_label",
            "timepoint_quality_warnings",
            "timepoint_latest_age_days",
            "timepoint_freshness_status",
            "timepoint_freshness_label",
            "serp_timepoint_count",
            "latest_serp_at",
            "tracking_status",
        )
    }


def _compact_niche(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "niche_id",
            "name",
            "role",
            "status",
            "snapshot_id",
            "snapshot_at",
            "rank_source_first_at",
            "rank_source_latest_at",
            "rank_source_span_hours",
            "rank_source_span_days",
            "rank_source_alignment",
            "evidence_hash",
            "evidence_level",
            "keyword_count",
            "observed_product_count",
            "rank_coverage",
            "serp_coverage",
            "product_snapshot_coverage",
            "price_p25",
            "price_median",
            "price_p75",
            "review_median",
            "monthly_bought_median",
            "monthly_bought_total",
            "monthly_bought_coverage",
            "demand_cr3",
            "ad_density",
            "brand_product_cr3",
            "timepoint_count",
            "first_snapshot_at",
            "latest_snapshot_at",
            "warnings",
        )
    }


def _methodology() -> dict[str, Any]:
    return {
        "name": "研究项目决策门禁 V1",
        "version": REPORT_METHOD_VERSION,
        "readiness_is_opportunity_score": False,
        "trend_thresholds": {
            "preliminary": {"minimum_points": TREND_MIN_POINTS, "minimum_span_days": TREND_MIN_DAYS},
            "stable_short_term": {
                "minimum_points": TREND_STABLE_POINTS,
                "minimum_span_days": TREND_STABLE_DAYS,
            },
            "keyword_timepoint_quality": {
                "minimum_independent_window_hours": TREND_INDEPENDENT_WINDOW_HOURS,
                "rank_integrity_required": True,
                "invalid_rank_batch_effect": "仅保留商品出现范围，不计入关键词排名趋势",
            },
            "seasonality": "disabled",
        },
        "competition_pressure_thresholds": {
            "review_median": 1000,
            "ad_density": 0.35,
            "demand_cr3": 0.65,
            "brand_product_cr3": 0.60,
        },
        "freshness_thresholds_days": {"current": 7, "stale": 30},
        "rank_source_alignment": {
            "adjacent_max_hours": 24,
            "mixed_period_effect": "降低证据轴、需求轴和竞争轴置信表达，不改原始聚合值",
        },
        "financial_complete_fields": [
            "unit_purchase_cost",
            "unit_shipping_cost",
            "unit_fba_fee",
            "unit_referral_fee",
        ],
    }


def _axis(
    key: str,
    label: str,
    status: str,
    confidence: str,
    summary: str,
    *,
    facts: Iterable[dict[str, Any]] = (),
    caveats: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "status": status,
        "status_label": AXIS_STATUS_LABELS[status],
        "confidence": confidence,
        "confidence_label": CONFIDENCE_LABELS[confidence],
        "summary": summary,
        "facts": [row for row in facts if row.get("value") is not None],
        "caveats": list(caveats),
    }


def _fact(
    key: str,
    label: str,
    value: Any,
    display: str,
    source_type: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = source or {}
    return {
        "key": key,
        "label": label,
        "value": _json_safe(value),
        "display": display,
        "source_type": source_type,
        "source_id": source.get("snapshot_id") or source.get("niche_id"),
        "source_at": source.get("snapshot_at"),
    }


def _gate(
    key: str,
    label: str,
    passed: bool,
    critical: bool,
    detail: str,
    action: str,
    severity: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "passed": bool(passed),
        "critical_for_final_decision": bool(critical),
        "detail": detail,
        "action": action,
        "severity": severity,
        "severity_label": SEVERITY_LABELS[severity],
    }


def _due_item(key: str, label: str, status: str, detail: str) -> dict[str, Any]:
    labels = {"verified": "已核验", "partial": "部分核验", "not_verified": "未核验"}
    return {
        "key": key,
        "label": label,
        "status": status,
        "status_label": labels[status],
        "detail": detail,
        "source": "人工或官方资料",
    }


def _notes_by_type(notes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result = {"observation": [], "opportunity": [], "risk": [], "decision": []}
    for row in notes:
        note_type = str(row.get("note_type") or "observation")
        result.setdefault(note_type, []).append(row)
    return result


def _merge_rows(
    base_rows: Iterable[dict[str, Any]],
    supplement_rows: Iterable[dict[str, Any]],
    *,
    key: str,
) -> list[dict[str, Any]]:
    extras = {
        row.get(key): _json_safe(dict(row))
        for row in supplement_rows
        if row.get(key) is not None
    }
    result = []
    seen = set()
    for raw in base_rows:
        row = _json_safe(dict(raw))
        identity = row.get(key)
        row.update(extras.get(identity, {}))
        result.append(row)
        seen.add(identity)
    for identity, row in extras.items():
        if identity not in seen:
            result.append(row)
    return result


def _append_source_date(
    target: list[dict[str, Any]],
    source_type: str,
    name: Any,
    value: Any,
) -> None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return
    target.append(
        {
            "source_type": source_type,
            "name": str(name or "--"),
            "observed_at": parsed.isoformat(sep=" "),
            "_date": parsed.date(),
        }
    )


def _span_days(first: Any, latest: Any) -> int:
    start = _parse_datetime(first)
    end = _parse_datetime(latest)
    if start is None or end is None:
        return 0
    try:
        elapsed = end - start
    except TypeError:
        elapsed = end.replace(tzinfo=None) - start.replace(tzinfo=None)
    if elapsed.total_seconds() < 0:
        return 0
    return int(elapsed.total_seconds() // 86_400)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_safe(value) for key, value in row.items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _fingerprint(value: Any) -> str:
    payload = json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "--"
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number)):,}"
    return f"{number:,.2f}"


def _pct(value: Any) -> str:
    number = _number(value)
    return "--" if number is None else f"{number * 100:.1f}%"


def _format_hours(value: Any) -> str:
    hours = _number(value)
    if hours is None:
        return "--"
    if hours >= 24:
        return f"{hours / 24:.1f} 天"
    if hours >= 1:
        return f"{hours:.1f} 小时"
    return f"{hours * 60:.0f} 分钟"


def _excerpt(value: Any, maximum: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= maximum else text[: maximum - 1] + "…"


def _argument_lines(rows: list[dict[str, Any]], empty: str) -> list[str]:
    if not rows:
        return [f"- {empty}"]
    return [
        f"- **{row.get('title') or '--'}**：{row.get('detail') or '--'}"
        f"（{row.get('source') or '来源未标注'}）"
        for row in rows
    ]


def _md(value: Any) -> str:
    text = str(value if value not in (None, "") else "--")
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _positive_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ResearchDecisionReportError(f"{label}必须是正整数") from exc
    if result < 1:
        raise ResearchDecisionReportError(f"{label}必须是正整数")
    return result
