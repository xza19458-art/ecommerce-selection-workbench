"""Read-only P7 shadow scoring and replay.

The V2 model intentionally does not write ``product_scores`` or replace the
existing recommendation ranking.  It scores a product inside one keyword's
latest complete collection batch and keeps opportunity, risk, and evidence
confidence as separate axes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence

from database.mysql_client import MySQLClient
from services.trend_analysis import TrendAssessment, assess_product_trend


MODEL_VERSION = "selection-score-v2-shadow-2026.07"
MODEL_SCOPE = "product_keyword_latest_batch"
RANK_BASIS = "latest_keyword_batch_v2"
CALIBRATION_VERSION = "selection-score-v2-calibration-v1"
CALIBRATION_SAMPLING_METHOD = "seeded_quantile_v1"
MAX_CALIBRATION_EXCLUDED_ASINS = 300
_RANK_USABLE = {"page_first", "batch_continuous"}
_RECOMMENDATION_LABELS = {
    "priority_validate": "优先人工验证",
    "observe": "可进入观察池",
    "benchmark_only": "仅适合作为对标",
    "pause": "暂缓",
}
_CONFIDENCE_BUCKETS = {
    "high": "高置信",
    "medium": "中置信",
    "low": "低置信",
}
_CALIBRATION_JUDGMENTS = {
    "pending": "未复核",
    "reasonable": "结论合理",
    "too_optimistic": "过于乐观",
    "too_conservative": "过于保守",
    "evidence_issue": "证据异常",
    "needs_collection": "需要补采",
}
_CALIBRATION_FLAG_META = {
    "strategy_disagreement": ("策略分歧", "attention", 5),
    "single_snapshot": ("单点趋势", "attention", 4),
    "short_trend": ("短周期趋势", "attention", 2),
    "low_confidence_high_opportunity": ("低置信高机会", "attention", 5),
    "price_uncalibrated": ("价格未按类目校准", "limitation", 1),
    "rank_evidence_gap": ("排名证据不足", "attention", 3),
    "missing_core_fields": ("核心字段缺失", "attention", 3),
    "multi_keyword_context": ("同商品多关键词", "context", 1),
    "high_opportunity_high_risk": ("高机会高风险", "context", 3),
    "threshold_boundary": ("接近建议阈值", "attention", 4),
    "promotion_sensitive": ("促销敏感", "attention", 4),
}


class ScoringV2Error(ValueError):
    """Raised for invalid replay filters or model strategy names."""


@dataclass(frozen=True)
class StrategyProfile:
    code: str
    label: str
    description: str
    opportunity_weights: Mapping[str, float]
    risk_weights: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "description": self.description,
            "opportunity_weights": dict(self.opportunity_weights),
            "risk_weights": dict(self.risk_weights),
        }


STRATEGIES: dict[str, StrategyProfile] = {
    "balanced": StrategyProfile(
        code="balanced",
        label="均衡研究",
        description="同时观察需求、趋势、接受度、自然可见度和进入风险。",
        opportunity_weights={
            "demand": 0.35,
            "growth": 0.20,
            "acceptance": 0.20,
            "visibility": 0.20,
            "improvement_space": 0.05,
        },
        risk_weights={
            "review_barrier": 0.35,
            "incumbent_pressure": 0.25,
            "quality_risk": 0.15,
            "price_risk": 0.15,
            "promo_risk": 0.10,
        },
    ),
    "low_budget": StrategyProfile(
        code="low_budget",
        label="低资金",
        description="更重视评论壁垒、价格容错和头部占位压力。",
        opportunity_weights={
            "demand": 0.30,
            "growth": 0.15,
            "acceptance": 0.25,
            "visibility": 0.15,
            "improvement_space": 0.15,
        },
        risk_weights={
            "review_barrier": 0.40,
            "incumbent_pressure": 0.20,
            "quality_risk": 0.10,
            "price_risk": 0.25,
            "promo_risk": 0.05,
        },
    ),
    "differentiation": StrategyProfile(
        code="differentiation",
        label="差异化",
        description="提高评分改良空间权重，同时保留质量与促销风险。",
        opportunity_weights={
            "demand": 0.25,
            "growth": 0.15,
            "acceptance": 0.10,
            "visibility": 0.10,
            "improvement_space": 0.40,
        },
        risk_weights={
            "review_barrier": 0.25,
            "incumbent_pressure": 0.20,
            "quality_risk": 0.25,
            "price_risk": 0.10,
            "promo_risk": 0.20,
        },
    ),
    "trend": StrategyProfile(
        code="trend",
        label="趋势型",
        description="提高持续增长权重；趋势证据不足时禁止输出强推荐。",
        opportunity_weights={
            "demand": 0.25,
            "growth": 0.45,
            "acceptance": 0.10,
            "visibility": 0.15,
            "improvement_space": 0.05,
        },
        risk_weights={
            "review_barrier": 0.25,
            "incumbent_pressure": 0.20,
            "quality_risk": 0.10,
            "price_risk": 0.10,
            "promo_risk": 0.35,
        },
    ),
}


_SORT_FIELDS = {
    "opportunity_score",
    "risk_score",
    "confidence_score",
    "legacy_total_score",
    "monthly_bought",
    "review_count",
    "rating",
    "price",
    "organic_rank",
    "snapshot_at",
}


def get_scoring_v2_model() -> dict[str, Any]:
    return {
        "version": MODEL_VERSION,
        "status": "shadow_readonly",
        "scope": MODEL_SCOPE,
        "rank_basis": RANK_BASIS,
        "strategies": [profile.to_dict() for profile in STRATEGIES.values()],
        "recommendations": dict(_RECOMMENDATION_LABELS),
        "missing_value_policy": "分项按中性 50 处理，并在置信度轴扣减；不把缺失伪装成真实 0 分。",
        "boundaries": [
            "近月购买量是 Amazon 前台下界代理，不是精确销量。",
            "自然序位是已采集页面中的估算，不是 Amazon 内部真实排名。",
            "价格风险尚未按类目校准，只能作为跨类目通用启发式。",
            "BSR、经验转化率、利润、评论痛点和图谱关系尚未进入本模型。",
            "影子结果不写 product_scores，也不改变推荐榜、商品池或关键词机会。",
        ],
    }


def fetch_scoring_v2_replay(
    *,
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
    client: MySQLClient | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Replay V2 against every keyword's latest complete rank batch.

    All filtering and sorting happens before pagination, so page ordering is a
    global ordering over the replay result rather than a current-page sort.
    """

    profile = _strategy(strategy)
    limit_value = _bounded_int(limit, default=50, minimum=1, maximum=200)
    offset_value = _bounded_int(offset, default=0, minimum=0, maximum=10_000_000)
    marketplace_value = str(marketplace or "US").strip().upper() or "US"
    search = str(keyword or "").strip()
    recommendation_value = _normalize_recommendation(recommendation)
    min_confidence_value = _optional_score(min_confidence, "最低置信度")
    max_risk_value = _optional_score(max_risk, "最高风险分")
    sort_value = sort_by if sort_by in _SORT_FIELDS else "opportunity_score"
    direction = "asc" if str(sort_dir or "").lower() == "asc" else "desc"

    db = client or MySQLClient()
    contexts, product_histories, rank_histories = _load_replay_evidence(db, marketplace_value, search)
    calculated_at = as_of or datetime.now()

    rows = [
        score_shadow_context(
            context,
            product_histories.get(int(context["product_id"]), []),
            rank_histories.get((int(context["keyword_id"]), int(context["product_id"])), []),
            strategy=profile.code,
            as_of=calculated_at,
        )
        for context in contexts
    ]
    if recommendation_value:
        rows = [row for row in rows if row["recommendation_code"] == recommendation_value]
    if min_confidence_value is not None:
        rows = [row for row in rows if row["confidence_score"] >= min_confidence_value]
    if max_risk_value is not None:
        rows = [row for row in rows if row["risk_score"] <= max_risk_value]

    rows = _sort_rows(rows, sort_value, direction)
    total = len(rows)
    page_rows = rows[offset_value : offset_value + limit_value]
    return {
        "rows": page_rows,
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "sort_by": sort_value,
        "sort_dir": direction,
        "strategy": profile.to_dict(),
        "summary": _build_replay_summary(rows),
        "model": get_scoring_v2_model(),
        "generated_at": calculated_at.isoformat(sep=" ", timespec="seconds"),
    }


def fetch_scoring_v2_calibration(
    *,
    sample_per_bucket: int = 2,
    marketplace: str = "US",
    keyword: str | None = None,
    strategy: str = "balanced",
    sample_seed: str = "baseline",
    exclude_asins: Iterable[str] = (),
    client: MySQLClient | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build a reproducible seeded sample without writing calibration state."""

    profile = _strategy(strategy)
    sample_count = _bounded_int(sample_per_bucket, default=2, minimum=1, maximum=8)
    marketplace_value = str(marketplace or "US").strip().upper() or "US"
    search = str(keyword or "").strip()
    calculated_at = as_of or datetime.now()
    db = client or MySQLClient()
    contexts, product_histories, rank_histories = _load_replay_evidence(db, marketplace_value, search)

    strategy_rows: dict[str, list[dict[str, Any]]] = {}
    for strategy_code in STRATEGIES:
        strategy_rows[strategy_code] = [
            score_shadow_context(
                context,
                product_histories.get(int(context["product_id"]), []),
                rank_histories.get((int(context["keyword_id"]), int(context["product_id"])), []),
                strategy=strategy_code,
                as_of=calculated_at,
            )
            for context in contexts
        ]

    result = build_scoring_v2_calibration(
        strategy_rows,
        strategy=profile.code,
        sample_per_bucket=sample_count,
        sample_seed=sample_seed,
        exclude_asins=exclude_asins,
    )
    result.update(
        {
            "strategy": profile.to_dict(),
            "sample_per_bucket": sample_count,
            "model": get_scoring_v2_model(),
            "calibration": {
                "version": CALIBRATION_VERSION,
                "status": "shadow_readonly_manual_review",
                "judgments": [
                    {"code": code, "label": label}
                    for code, label in _CALIBRATION_JUDGMENTS.items()
                ],
                "flag_labels": {
                    code: {"label": meta[0], "severity": meta[1]}
                    for code, meta in _CALIBRATION_FLAG_META.items()
                },
                "review_storage": "browser_draft_explicit_local_json_export",
                "writes_database": False,
            },
            "generated_at": calculated_at.isoformat(sep=" ", timespec="seconds"),
        }
    )
    return result


def build_scoring_v2_calibration(
    strategy_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    strategy: str = "balanced",
    sample_per_bucket: int = 2,
    sample_seed: str = "baseline",
    exclude_asins: Iterable[str] = (),
) -> dict[str, Any]:
    """Create calibration diagnostics from already-scored strategy rows."""

    profile = _strategy(strategy)
    sample_count = _bounded_int(sample_per_bucket, default=2, minimum=1, maximum=8)
    seed = _normalize_calibration_seed(sample_seed)
    excluded_asins = _normalize_calibration_excluded_asins(exclude_asins)
    source_primary_rows = [dict(row) for row in strategy_rows.get(profile.code, ())]
    eligible_strategy_rows = {
        code: [
            dict(row)
            for row in rows
            if str(row.get("asin") or "").strip().upper() not in excluded_asins
        ]
        for code, rows in strategy_rows.items()
        if code in STRATEGIES
    }
    primary_rows = eligible_strategy_rows.get(profile.code, [])
    indexes = {
        code: {_calibration_context_key(row): row for row in rows}
        for code, rows in eligible_strategy_rows.items()
    }
    asin_context_counts = Counter(str(row.get("asin") or "") for row in primary_rows if row.get("asin"))
    annotated: list[dict[str, Any]] = []
    flag_counts: Counter[str] = Counter()

    for raw in primary_rows:
        row = dict(raw)
        context_key = _calibration_context_key(row)
        outcomes: dict[str, dict[str, Any]] = {}
        for code, strategy_profile in STRATEGIES.items():
            compared = indexes.get(code, {}).get(context_key)
            if compared is None:
                continue
            outcomes[code] = {
                "strategy_label": strategy_profile.label,
                "opportunity_score": compared.get("opportunity_score"),
                "risk_score": compared.get("risk_score"),
                "confidence_score": compared.get("confidence_score"),
                "recommendation_code": compared.get("recommendation_code"),
                "recommendation_label": compared.get("recommendation_label"),
            }
        confidence_bucket = _calibration_confidence_bucket(row.get("confidence_score"))
        bucket_key = f"{row.get('recommendation_code')}:{confidence_bucket}"
        flags = _calibration_flags(
            row,
            outcomes,
            asin_context_count=asin_context_counts.get(str(row.get("asin") or ""), 0),
        )
        flag_counts.update(flag["code"] for flag in flags)
        row.update(
            {
                "context_key": context_key,
                "sample_key": (
                    f"{MODEL_VERSION}:{profile.code}:"
                    f"{int(row.get('keyword_id') or 0)}:{int(row.get('product_id') or 0)}"
                ),
                "confidence_bucket": confidence_bucket,
                "confidence_bucket_label": _CONFIDENCE_BUCKETS[confidence_bucket],
                "sample_bucket": bucket_key,
                "sample_bucket_label": (
                    f"{_RECOMMENDATION_LABELS.get(str(row.get('recommendation_code')), '未知建议')}"
                    f" · {_CONFIDENCE_BUCKETS[confidence_bucket]}"
                ),
                "strategy_outcomes": outcomes,
                "calibration_flags": flags,
                "calibration_priority": sum(
                    _CALIBRATION_FLAG_META.get(flag["code"], ("", "", 0))[2]
                    for flag in flags
                ),
            }
        )
        annotated.append(row)

    samples, bucket_rows = _select_calibration_samples(
        annotated,
        sample_count,
        sample_seed=seed,
    )
    strategy_distributions: dict[str, dict[str, int]] = {}
    strategy_changes: dict[str, int] = {}
    primary_index = indexes.get(profile.code, {})
    for code, rows in eligible_strategy_rows.items():
        counts = Counter(str(row.get("recommendation_code") or "unknown") for row in rows)
        strategy_distributions[code] = {
            recommendation_code: counts.get(recommendation_code, 0)
            for recommendation_code in _RECOMMENDATION_LABELS
        }
        compared_index = indexes.get(code, {})
        common_keys = set(primary_index) & set(compared_index)
        strategy_changes[code] = sum(
            1
            for key in common_keys
            if primary_index[key].get("recommendation_code") != compared_index[key].get("recommendation_code")
        )

    return {
        "rows": samples,
        "sampling": {
            "method": CALIBRATION_SAMPLING_METHOD,
            "seed": seed,
            "excluded_asin_count": len(
                {
                    str(row.get("asin") or "").strip().upper()
                    for row in source_primary_rows
                    if str(row.get("asin") or "").strip().upper() in excluded_asins
                }
            ),
        },
        "summary": {
            "source_context_count": len(source_primary_rows),
            "context_count": len(primary_rows),
            "product_count": len({row.get("asin") for row in primary_rows if row.get("asin")}),
            "sample_count": len(samples),
            "bucket_count": len(bucket_rows),
            "multi_context_asin_count": sum(1 for count in asin_context_counts.values() if count > 1),
            "strategy_disagreement_count": flag_counts.get("strategy_disagreement", 0),
            "flag_counts": {
                code: flag_counts.get(code, 0)
                for code in _CALIBRATION_FLAG_META
            },
            "strategy_distributions": strategy_distributions,
            "strategy_changes": strategy_changes,
            "buckets": bucket_rows,
        },
    }


def score_shadow_context(
    context: Mapping[str, Any],
    product_history: Iterable[Mapping[str, Any]],
    keyword_rank_history: Iterable[Mapping[str, Any]] = (),
    *,
    strategy: str = "balanced",
    strategy_profile: StrategyProfile | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Score one product-keyword context without database access or writes."""

    profile = strategy_profile or _strategy(strategy)
    row = _normalized_context(context)
    product_history_rows = list(product_history)
    keyword_rank_history_rows = list(keyword_rank_history)
    merged_history = _context_history(product_history_rows, keyword_rank_history_rows, row)
    trend = assess_product_trend(merged_history)
    trend_signal_count = _trend_signal_count(merged_history)
    rank_confidence = str(row.get("rank_confidence") or "unknown")
    rank_usable = rank_confidence in _RANK_USABLE and not bool(row.get("is_sponsored"))

    demand_score, demand_status, demand_reason = _demand_signal(row.get("monthly_bought"))
    acceptance_score, acceptance_status, acceptance_reason = _acceptance_signal(
        row.get("rating"), row.get("review_count")
    )
    visibility_score, visibility_status, visibility_reason = _visibility_signal(
        row.get("organic_rank"), rank_confidence, bool(row.get("is_sponsored"))
    )
    growth_score, growth_status, growth_reason = _growth_signal(trend, trend_signal_count)
    improvement_score, improvement_status, improvement_reason = _improvement_signal(
        row.get("monthly_bought"), row.get("rating"), demand_score
    )

    opportunity_raw = {
        "demand": ("需求下界", demand_score, demand_status, demand_reason, {"monthly_bought": row.get("monthly_bought")}),
        "growth": (
            "趋势增长",
            growth_score,
            growth_status,
            growth_reason,
            {
                "sample_size": trend.sample_size,
                "span_days": trend.span_days,
                "trend_confidence": trend.confidence,
                "trend_confidence_score": trend.confidence_score,
            },
        ),
        "acceptance": (
            "评分接受度",
            acceptance_score,
            acceptance_status,
            acceptance_reason,
            {"rating": row.get("rating"), "review_count": row.get("review_count")},
        ),
        "visibility": (
            "自然可见度",
            visibility_score,
            visibility_status,
            visibility_reason,
            {
                "organic_rank": row.get("organic_rank"),
                "rank_confidence": rank_confidence,
                "is_sponsored": bool(row.get("is_sponsored")),
            },
        ),
        "improvement_space": (
            "评分改良空间代理",
            improvement_score,
            improvement_status,
            improvement_reason,
            {"monthly_bought": row.get("monthly_bought"), "rating": row.get("rating")},
        ),
    }

    review_risk, review_status, review_reason = _review_barrier(row.get("review_count"))
    incumbent_risk, incumbent_status, incumbent_reason = _incumbent_pressure(
        row.get("monthly_bought"), row.get("organic_rank"), rank_usable, demand_score, visibility_score
    )
    quality_risk, quality_status, quality_reason = _quality_risk(row.get("rating"), row.get("review_count"))
    price_risk, price_status, price_reason = _price_risk(row.get("price"))
    promo_risk, promo_status, promo_reason = _promo_risk(row.get("is_deal"), trend)
    risk_raw = {
        "review_barrier": (
            "评论壁垒",
            review_risk,
            review_status,
            review_reason,
            {"review_count": row.get("review_count")},
        ),
        "incumbent_pressure": (
            "头部占位压力",
            incumbent_risk,
            incumbent_status,
            incumbent_reason,
            {
                "monthly_bought": row.get("monthly_bought"),
                "organic_rank": row.get("organic_rank") if rank_usable else None,
            },
        ),
        "quality_risk": (
            "质量/退货风险",
            quality_risk,
            quality_status,
            quality_reason,
            {"rating": row.get("rating"), "review_count": row.get("review_count")},
        ),
        "price_risk": (
            "价格与利润容错",
            price_risk,
            price_status,
            price_reason,
            {"price": row.get("price"), "category_calibrated": False},
        ),
        "promo_risk": (
            "促销依赖风险",
            promo_risk,
            promo_status,
            promo_reason,
            {"is_deal": bool(row.get("is_deal")), "promo_warning": trend.promo_warning},
        ),
    }

    opportunity_components = _weighted_components(opportunity_raw, profile.opportunity_weights)
    risk_components = _weighted_components(risk_raw, profile.risk_weights)
    opportunity_score = _axis_score(opportunity_components)
    risk_score = _axis_score(risk_components)

    confidence_components = _confidence_components(
        row,
        trend,
        trend_signal_count=trend_signal_count,
        rank_usable=rank_usable,
        as_of=as_of or datetime.now(),
    )
    confidence_score = _axis_score(confidence_components)
    confidence_level = _confidence_level(confidence_score)
    recommendation_code, recommendation_reason = _recommendation(
        profile,
        opportunity_score=opportunity_score,
        risk_score=risk_score,
        confidence_score=confidence_score,
        trend=trend,
        trend_signal_count=trend_signal_count,
        opportunity_components=opportunity_components,
        risk_components=risk_components,
    )
    supporting = _top_component_reasons(opportunity_components, reverse=True)
    opposing = _top_component_reasons(risk_components, reverse=True)
    if confidence_score < 55:
        opposing.append(f"模型置信度仅 {confidence_score:.0f}，应先补充同关键词多时间点快照")

    return {
        "product_id": row.get("product_id"),
        "marketplace": row.get("marketplace"),
        "asin": row.get("asin"),
        "title": row.get("title_zh") or row.get("title"),
        "title_original": row.get("title"),
        "title_zh": row.get("title_zh"),
        "product_url": row.get("product_url"),
        "image_url": row.get("image_url"),
        "keyword_id": row.get("keyword_id"),
        "keyword": row.get("keyword"),
        "snapshot_at": _display_value(row.get("snapshot_at")),
        "price": row.get("price"),
        "rating": row.get("rating"),
        "review_count": row.get("review_count"),
        "monthly_bought": row.get("monthly_bought"),
        "organic_rank": row.get("organic_rank"),
        "rank_confidence": rank_confidence,
        "is_deal": bool(row.get("is_deal")),
        "legacy_total_score": row.get("legacy_total_score"),
        "legacy_growth_score": row.get("legacy_growth_score"),
        "opportunity_score": opportunity_score,
        "risk_score": risk_score,
        "confidence_score": confidence_score,
        "confidence_level": confidence_level,
        "recommendation_code": recommendation_code,
        "recommendation_label": _RECOMMENDATION_LABELS[recommendation_code],
        "recommendation_reason": recommendation_reason,
        "supporting_reasons": supporting[:2],
        "opposing_reasons": opposing[:3],
        "opportunity_components": opportunity_components,
        "risk_components": risk_components,
        "confidence_components": confidence_components,
        "trend": _trend_to_dict(trend, trend_signal_count),
        "source_identity": {
            "model_version": MODEL_VERSION,
            "strategy": profile.code,
            "scope": MODEL_SCOPE,
            "rank_basis": RANK_BASIS,
            "keyword_id": row.get("keyword_id"),
            "rank_snapshot_id": row.get("rank_snapshot_id"),
            "product_snapshot_id": row.get("product_snapshot_id"),
            "snapshot_at": _display_value(row.get("snapshot_at")),
            "history_snapshot_ids": [item.get("snapshot_id") for item in merged_history if item.get("snapshot_id")],
            "keyword_rank_snapshot_ids": [
                item.get("rank_snapshot_id") for item in keyword_rank_history_rows if item.get("rank_snapshot_id")
            ],
            "writes_production_score": False,
        },
    }


def _load_replay_evidence(
    client: MySQLClient,
    marketplace: str,
    search: str,
) -> tuple[
    list[dict[str, Any]],
    dict[int, list[dict[str, Any]]],
    dict[tuple[int, int], list[dict[str, Any]]],
]:
    with client.connect() as conn:
        with conn.cursor() as cursor:
            contexts = _fetch_current_contexts(cursor, marketplace, search)
            product_ids = sorted({int(row["product_id"]) for row in contexts})
            context_pairs = {
                (int(row["keyword_id"]), int(row["product_id"]))
                for row in contexts
                if row.get("keyword_id") is not None and row.get("product_id") is not None
            }
            product_histories = _fetch_product_histories(cursor, product_ids)
            rank_histories = _fetch_keyword_rank_histories(cursor, context_pairs)
    return contexts, product_histories, rank_histories


def load_product_scoring_evidence(
    asin: str,
    *,
    marketplace: str = "US",
    keyword: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    """Load one exact product-keyword context for read-only profile scoring."""

    asin_value = str(asin or "").strip().upper()
    if len(asin_value) != 10 or not asin_value.isalnum():
        raise ScoringV2Error("ASIN 必须是 10 位字母或数字")
    marketplace_value = str(marketplace or "US").strip().upper() or "US"
    db = client or MySQLClient()
    contexts, product_histories, rank_histories = _load_replay_evidence(
        db,
        marketplace_value,
        asin_value,
    )
    exact = [
        row
        for row in contexts
        if str(row.get("asin") or "").strip().upper() == asin_value
    ]
    keyword_value = " ".join(str(keyword or "").strip().lower().split())
    if keyword_value:
        exact = [
            row
            for row in exact
            if " ".join(str(row.get("keyword") or "").strip().lower().split()) == keyword_value
        ]
    product_only = False
    if not exact and not keyword_value:
        fallback_context, fallback_history = _load_product_only_evidence(
            db,
            marketplace_value,
            asin_value,
        )
        if fallback_context:
            exact = [fallback_context]
            product_id = int(fallback_context["product_id"])
            product_histories[product_id] = fallback_history
            product_only = True
    if not exact:
        suffix = f"、关键词“{keyword}”" if keyword_value else ""
        raise ScoringV2Error(f"未找到 {marketplace_value} 站点 ASIN {asin_value}{suffix} 的评分上下文")
    exact.sort(
        key=lambda row: (
            str(row.get("snapshot_at") or ""),
            str(row.get("keyword") or "").casefold(),
        ),
        reverse=True,
    )
    context = exact[0]
    product_id = int(context["product_id"])
    keyword_id = int(context["keyword_id"]) if context.get("keyword_id") is not None else None
    return {
        "context": context,
        "product_history": product_histories.get(product_id, []),
        "keyword_rank_history": rank_histories.get((keyword_id, product_id), []) if keyword_id is not None else [],
        "available_contexts": [
            {
                "keyword_id": row.get("keyword_id"),
                "keyword": row.get("keyword"),
                "snapshot_at": _display_value(row.get("snapshot_at")),
                "context_scope": row.get("context_scope") or "product_keyword",
            }
            for row in exact
        ],
        "selection_warning": (
            "该商品尚未关联关键词排名；本次使用最近商品搜索快照做降置信试算，自然序位保持未知并按中性处理。"
            if product_only
            else None
            if keyword_value or len(exact) == 1
            else "该商品存在多个关键词评分上下文；未指定关键词时使用最近采集上下文。"
        ),
    }


def load_product_scoring_evidence_batch(
    asins: Sequence[str],
    *,
    marketplace: str = "US",
    preferred_keywords: Mapping[str, str] | None = None,
    client: MySQLClient | None = None,
) -> dict[str, dict[str, Any]]:
    """Load exact scoring evidence for a bounded product sample in one DB pass.

    The helper mirrors :func:`load_product_scoring_evidence`, but avoids running
    the latest-keyword and history queries once per product.  Missing products
    are omitted; callers can report them as sample failures without inventing
    evidence.
    """

    asin_values: list[str] = []
    for raw in asins:
        value = str(raw or "").strip().upper()
        if len(value) != 10 or not value.isalnum():
            raise ScoringV2Error(f"非法 ASIN：{raw!r}")
        if value not in asin_values:
            asin_values.append(value)
    if not asin_values:
        return {}
    if len(asin_values) > 100:
        raise ScoringV2Error("单次批量评分证据最多读取 100 个 ASIN")

    marketplace_value = str(marketplace or "US").strip().upper() or "US"
    preferred = {
        str(asin or "").strip().upper(): " ".join(str(keyword or "").strip().lower().split())
        for asin, keyword in dict(preferred_keywords or {}).items()
        if str(keyword or "").strip()
    }
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            contexts = _fetch_current_contexts_for_asins(cursor, marketplace_value, asin_values)
            product_only = _fetch_product_only_contexts(cursor, marketplace_value, asin_values)

            contexts_by_asin: dict[str, list[dict[str, Any]]] = {}
            for row in contexts:
                contexts_by_asin.setdefault(str(row.get("asin") or "").upper(), []).append(row)

            selected: dict[str, tuple[dict[str, Any], bool]] = {}
            for asin in asin_values:
                exact = list(contexts_by_asin.get(asin) or [])
                keyword_value = preferred.get(asin, "")
                if keyword_value:
                    exact = [
                        row
                        for row in exact
                        if " ".join(str(row.get("keyword") or "").strip().lower().split()) == keyword_value
                    ]
                exact.sort(
                    key=lambda row: (
                        str(row.get("snapshot_at") or ""),
                        str(row.get("keyword") or "").casefold(),
                    ),
                    reverse=True,
                )
                if exact:
                    selected[asin] = (exact[0], False)
                elif not keyword_value and asin in product_only:
                    selected[asin] = (product_only[asin], True)

            product_ids = sorted({int(row[0]["product_id"]) for row in selected.values()})
            context_pairs = {
                (int(row[0]["keyword_id"]), int(row[0]["product_id"]))
                for row in selected.values()
                if row[0].get("keyword_id") is not None
            }
            product_histories = _fetch_product_histories(cursor, product_ids)
            rank_histories = _fetch_keyword_rank_histories(cursor, context_pairs)

    payload: dict[str, dict[str, Any]] = {}
    for asin, (context, is_product_only) in selected.items():
        product_id = int(context["product_id"])
        keyword_id = int(context["keyword_id"]) if context.get("keyword_id") is not None else None
        available = contexts_by_asin.get(asin) or []
        payload[asin] = {
            "context": context,
            "product_history": product_histories.get(product_id, []),
            "keyword_rank_history": rank_histories.get((keyword_id, product_id), []) if keyword_id is not None else [],
            "available_contexts": [
                {
                    "keyword_id": row.get("keyword_id"),
                    "keyword": row.get("keyword"),
                    "snapshot_at": _display_value(row.get("snapshot_at")),
                    "context_scope": row.get("context_scope") or "product_keyword",
                }
                for row in available
            ],
            "selection_warning": (
                "该商品尚未关联关键词排名；本次使用最近商品搜索快照做降置信试算，自然序位保持未知并按中性处理。"
                if is_product_only
                else None
                if preferred.get(asin) or len(available) == 1
                else "该商品存在多个关键词评分上下文；批量验证使用最近采集上下文。"
            ),
        }
    return payload


def _fetch_current_contexts_for_asins(
    cursor: Any,
    marketplace: str,
    asins: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start in range(0, len(asins), 250):
        chunk = list(asins[start:start + 250])
        placeholders = ", ".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT
              p.id AS product_id, p.marketplace, p.asin, p.title, p.title_zh,
              p.product_url, p.image_url,
              k.id AS keyword_id, k.keyword,
              krs.id AS rank_snapshot_id, krs.snapshot_at, krs.page_no,
              krs.organic_rank, krs.is_sponsored,
              snap.id AS product_snapshot_id, snap.price, snap.rating,
              snap.review_count, snap.monthly_bought, snap.is_deal, snap.raw_json,
              ps.total_score AS legacy_total_score,
              ps.growth_score AS legacy_growth_score,
              'product_keyword' AS context_scope
            FROM keyword_rank_snapshots krs
            JOIN (
              SELECT krs2.keyword_id, MAX(krs2.snapshot_at) AS snapshot_at
              FROM keyword_rank_snapshots krs2
              JOIN keywords k2 ON k2.id = krs2.keyword_id
              WHERE k2.marketplace = %s
              GROUP BY krs2.keyword_id
            ) latest
              ON latest.keyword_id = krs.keyword_id
             AND latest.snapshot_at = krs.snapshot_at
            JOIN keywords k ON k.id = krs.keyword_id
            JOIN products p ON p.id = krs.product_id
            LEFT JOIN product_snapshots snap
              ON snap.product_id = krs.product_id
             AND snap.snapshot_at = krs.snapshot_at
            LEFT JOIN product_scores ps
              ON ps.product_id = krs.product_id
             AND (ps.keyword_id <=> krs.keyword_id)
             AND ps.score_date = (
               SELECT MAX(ps2.score_date)
               FROM product_scores ps2
               WHERE ps2.product_id = krs.product_id
                 AND (ps2.keyword_id <=> krs.keyword_id)
             )
            WHERE k.marketplace = %s AND p.marketplace = %s
              AND p.asin IN ({placeholders})
            ORDER BY p.asin, krs.snapshot_at DESC, k.keyword
            """,
            [marketplace, marketplace, marketplace, *chunk],
        )
        rows.extend(_normalized_context(row) for row in cursor.fetchall())
    return rows


def _fetch_product_only_contexts(
    cursor: Any,
    marketplace: str,
    asins: Sequence[str],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for start in range(0, len(asins), 250):
        chunk = list(asins[start:start + 250])
        placeholders = ", ".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT
              p.id AS product_id, p.marketplace, p.asin, p.title, p.title_zh,
              p.product_url, p.image_url,
              NULL AS keyword_id, NULL AS keyword,
              NULL AS rank_snapshot_id, snap.snapshot_at, NULL AS page_no,
              NULL AS organic_rank, 0 AS is_sponsored,
              snap.id AS product_snapshot_id, snap.price, snap.rating,
              snap.review_count, snap.monthly_bought, snap.is_deal, snap.raw_json,
              ps.total_score AS legacy_total_score,
              ps.growth_score AS legacy_growth_score,
              'product_only' AS context_scope
            FROM products p
            JOIN product_snapshots snap
              ON snap.id = (
                SELECT snap2.id
                FROM product_snapshots snap2
                WHERE snap2.product_id = p.id
                ORDER BY snap2.snapshot_at DESC, snap2.id DESC
                LIMIT 1
              )
            LEFT JOIN product_scores ps
              ON ps.id = (
                SELECT ps2.id
                FROM product_scores ps2
                WHERE ps2.product_id = p.id AND ps2.keyword_id IS NULL
                ORDER BY ps2.score_date DESC, ps2.id DESC
                LIMIT 1
              )
            WHERE p.marketplace = %s AND p.asin IN ({placeholders})
            """,
            [marketplace, *chunk],
        )
        for raw in cursor.fetchall():
            row = _normalized_context(raw)
            rows[str(row.get("asin") or "").upper()] = row
    return rows


def _load_product_only_evidence(
    client: MySQLClient,
    marketplace: str,
    asin: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Load a rank-neutral fallback only when the caller did not request a keyword."""

    with client.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  p.id AS product_id, p.marketplace, p.asin, p.title, p.title_zh,
                  p.product_url, p.image_url,
                  NULL AS keyword_id, NULL AS keyword,
                  NULL AS rank_snapshot_id, snap.snapshot_at, NULL AS page_no,
                  NULL AS organic_rank, 0 AS is_sponsored,
                  snap.id AS product_snapshot_id, snap.price, snap.rating,
                  snap.review_count, snap.monthly_bought, snap.is_deal, snap.raw_json,
                  ps.total_score AS legacy_total_score,
                  ps.growth_score AS legacy_growth_score,
                  'product_only' AS context_scope
                FROM products p
                LEFT JOIN product_snapshots snap
                  ON snap.id = (
                    SELECT snap2.id
                    FROM product_snapshots snap2
                    WHERE snap2.product_id = p.id
                    ORDER BY snap2.snapshot_at DESC, snap2.id DESC
                    LIMIT 1
                  )
                LEFT JOIN product_scores ps
                  ON ps.id = (
                    SELECT ps2.id
                    FROM product_scores ps2
                    WHERE ps2.product_id = p.id AND ps2.keyword_id IS NULL
                    ORDER BY ps2.score_date DESC, ps2.id DESC
                    LIMIT 1
                  )
                WHERE p.marketplace = %s AND p.asin = %s
                  AND snap.id IS NOT NULL
                LIMIT 1
                """,
                (marketplace, asin),
            )
            raw = cursor.fetchone()
            if not raw:
                return None, []
            context = _normalized_context(raw)
            histories = _fetch_product_histories(cursor, [int(context["product_id"])])
    return context, histories.get(int(context["product_id"]), [])


def _fetch_current_contexts(cursor: Any, marketplace: str, search: str) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = [marketplace, marketplace, marketplace]
    if search:
        pattern = f"%{search}%"
        where = """
          AND (
            k.keyword LIKE %s OR p.asin LIKE %s OR p.title LIKE %s
            OR COALESCE(p.title_zh, '') LIKE %s
          )
        """
        params.extend((pattern, pattern, pattern, pattern))
    cursor.execute(
        f"""
        SELECT
          p.id AS product_id, p.marketplace, p.asin, p.title, p.title_zh,
          p.product_url, p.image_url,
          k.id AS keyword_id, k.keyword,
          krs.id AS rank_snapshot_id, krs.snapshot_at, krs.page_no,
          krs.organic_rank, krs.is_sponsored,
          snap.id AS product_snapshot_id, snap.price, snap.rating,
          snap.review_count, snap.monthly_bought, snap.is_deal, snap.raw_json,
          ps.total_score AS legacy_total_score,
          ps.growth_score AS legacy_growth_score
        FROM keyword_rank_snapshots krs
        JOIN (
          SELECT krs2.keyword_id, MAX(krs2.snapshot_at) AS snapshot_at
          FROM keyword_rank_snapshots krs2
          JOIN keywords k2 ON k2.id = krs2.keyword_id
          WHERE k2.marketplace = %s
          GROUP BY krs2.keyword_id
        ) latest
          ON latest.keyword_id = krs.keyword_id
         AND latest.snapshot_at = krs.snapshot_at
        JOIN keywords k ON k.id = krs.keyword_id
        JOIN products p ON p.id = krs.product_id
        LEFT JOIN product_snapshots snap
          ON snap.product_id = krs.product_id
         AND snap.snapshot_at = krs.snapshot_at
        LEFT JOIN product_scores ps
          ON ps.product_id = krs.product_id
         AND (ps.keyword_id <=> krs.keyword_id)
         AND ps.score_date = (
           SELECT MAX(ps2.score_date)
           FROM product_scores ps2
           WHERE ps2.product_id = krs.product_id
             AND (ps2.keyword_id <=> krs.keyword_id)
         )
        WHERE k.marketplace = %s AND p.marketplace = %s
        {where}
        ORDER BY k.keyword, p.asin
        """,
        params,
    )
    return [_normalized_context(row) for row in cursor.fetchall()]


def _fetch_product_histories(cursor: Any, product_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    histories: dict[int, list[dict[str, Any]]] = {}
    for chunk in _chunks(product_ids, 900):
        placeholders = ", ".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT id AS snapshot_id, product_id, snapshot_at, price, rating,
                   review_count, monthly_bought, is_deal
            FROM product_snapshots
            WHERE product_id IN ({placeholders})
            ORDER BY product_id, snapshot_at, id
            """,
            list(chunk),
        )
        for raw in cursor.fetchall():
            row = _normalize_value(raw)
            histories.setdefault(int(row["product_id"]), []).append(row)
    return histories


def _fetch_keyword_rank_histories(
    cursor: Any,
    context_pairs: set[tuple[int, int]],
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    histories: dict[tuple[int, int], list[dict[str, Any]]] = {}
    if not context_pairs:
        return histories
    keyword_ids = sorted({pair[0] for pair in context_pairs})
    product_ids = sorted({pair[1] for pair in context_pairs})
    keyword_placeholders = ", ".join(["%s"] * len(keyword_ids))
    for chunk in _chunks(product_ids, 850):
        product_placeholders = ", ".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT id AS rank_snapshot_id, keyword_id, product_id, snapshot_at,
                   page_no, organic_rank, is_sponsored
            FROM keyword_rank_snapshots
            WHERE keyword_id IN ({keyword_placeholders})
              AND product_id IN ({product_placeholders})
            ORDER BY keyword_id, product_id, snapshot_at, id
            """,
            [*keyword_ids, *chunk],
        )
        for raw in cursor.fetchall():
            row = _normalize_value(raw)
            pair = (int(row["keyword_id"]), int(row["product_id"]))
            if pair in context_pairs:
                histories.setdefault(pair, []).append(row)
    return histories


def _normalized_context(raw: Mapping[str, Any]) -> dict[str, Any]:
    row = _normalize_value(dict(raw))
    parsed = _parse_json(row.pop("raw_json", None))
    row["rank_confidence"] = _rank_confidence_from_keyword_rank(row, fallback=parsed.get("rank_confidence"))
    for key in ("product_id", "keyword_id", "rank_snapshot_id", "product_snapshot_id", "review_count", "monthly_bought", "organic_rank", "page_no"):
        if row.get(key) is not None:
            row[key] = int(row[key])
    for key in ("price", "rating", "legacy_total_score", "legacy_growth_score"):
        if row.get(key) is not None:
            row[key] = float(row[key])
    row["is_deal"] = bool(row.get("is_deal"))
    row["is_sponsored"] = bool(row.get("is_sponsored"))
    return row


def _rank_confidence_from_keyword_rank(row: Mapping[str, Any], *, fallback: Any = None) -> str:
    """Derive confidence from the keyword-rank row, not product snapshot raw JSON.

    A product snapshot can be overwritten by another keyword captured in the same
    hour. The keyword-rank table remains context-specific: sponsored rows are
    unusable, a non-null rank on page 1 is page-first evidence, and a non-null
    rank on later pages only survives ingestion when the batch was continuous.
    """

    if bool(row.get("is_sponsored")):
        return "sponsored"
    if row.get("organic_rank") is not None:
        try:
            page_no = int(row.get("page_no") or 0)
        except (TypeError, ValueError):
            page_no = 0
        return "page_first" if page_no <= 1 else "batch_continuous"
    fallback_value = str(fallback or "").strip()
    return fallback_value if fallback_value in {"unknown", "sponsored"} else "unknown"


def _context_history(
    product_history: Iterable[Mapping[str, Any]],
    keyword_rank_history: Iterable[Mapping[str, Any]],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    ranks_by_time: dict[str, Mapping[str, Any]] = {}
    for rank in keyword_rank_history:
        key = _time_key(rank.get("snapshot_at"))
        if not key:
            continue
        previous = ranks_by_time.get(key)
        if previous is None or _prefer_rank(rank, previous):
            ranks_by_time[key] = rank

    rows: list[dict[str, Any]] = []
    for raw in product_history:
        row = _normalize_value(dict(raw))
        rank = ranks_by_time.get(_time_key(row.get("snapshot_at")))
        row["organic_rank"] = None
        if rank and not bool(rank.get("is_sponsored")) and rank.get("organic_rank") is not None:
            row["organic_rank"] = int(rank["organic_rank"])
            row["rank_snapshot_id"] = rank.get("rank_snapshot_id")
        rows.append(row)
    if not rows and context.get("snapshot_at") is not None:
        rows.append(
            {
                "snapshot_id": context.get("product_snapshot_id"),
                "snapshot_at": context.get("snapshot_at"),
                "price": context.get("price"),
                "rating": context.get("rating"),
                "review_count": context.get("review_count"),
                "monthly_bought": context.get("monthly_bought"),
                "organic_rank": context.get("organic_rank"),
                "is_deal": context.get("is_deal"),
            }
        )
    return rows


def _prefer_rank(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    candidate_natural = not bool(candidate.get("is_sponsored")) and candidate.get("organic_rank") is not None
    current_natural = not bool(current.get("is_sponsored")) and current.get("organic_rank") is not None
    if candidate_natural != current_natural:
        return candidate_natural
    return int(candidate.get("rank_snapshot_id") or 0) > int(current.get("rank_snapshot_id") or 0)


def _demand_signal(value: Any) -> tuple[float, str, str]:
    monthly = _number(value)
    if monthly is None:
        return 50.0, "neutral_missing", "未采集近月购买量，需求分按中性 50 处理并降低置信度"
    score = _clamp(math.log10(max(0.0, monthly) + 1.0) / math.log10(20_001.0) * 100.0)
    return score, "observed", f"前台近月购买量下界约 {monthly:,.0f}，仅作为需求代理"


def _acceptance_signal(rating_value: Any, review_value: Any) -> tuple[float, str, str]:
    rating = _number(rating_value)
    reviews = _number(review_value)
    if rating is None:
        return 50.0, "neutral_missing", "评分缺失，评分接受度按中性 50 处理"
    raw = _clamp((rating - 3.5) / 1.5 * 100.0)
    reliability = _rating_reliability(reviews)
    score = 50.0 + (raw - 50.0) * reliability
    status = "observed" if reliability >= 0.65 else "low_confidence"
    review_text = "评论数缺失" if reviews is None else f"{reviews:,.0f} 条评论"
    return score, status, f"评分 {rating:.1f}，结合{review_text}收缩极端评分，避免少量五星被高估"


def _visibility_signal(rank_value: Any, confidence: str, sponsored: bool) -> tuple[float, str, str]:
    rank = _number(rank_value)
    if sponsored:
        return 50.0, "neutral_missing", "当前记录是广告观察，不计入自然可见度"
    if rank is None:
        return 50.0, "neutral_missing", "自然序位缺失，自然可见度按中性 50 处理"
    if confidence not in _RANK_USABLE:
        return 50.0, "low_confidence", f"自然序位估算为 {rank:.0f}，但排名置信标记为 {confidence}，暂按中性处理"
    if rank <= 10:
        score = 100.0 - max(0.0, rank - 1.0) * 1.5
    elif rank <= 50:
        score = 85.0 - (rank - 10.0) / 40.0 * 30.0
    elif rank <= 100:
        score = 55.0 - (rank - 50.0) / 50.0 * 20.0
    else:
        score = max(20.0, 35.0 - (rank - 100.0) / 200.0 * 15.0)
    return _clamp(score), "observed", f"同关键词最新完整批次中的自然序位估算约 {rank:.0f}"


def _growth_signal(trend: TrendAssessment, signal_count: int) -> tuple[float, str, str]:
    if signal_count < 2:
        return 50.0, "neutral_missing", "可比较的月购买量或同关键词位次不足 2 个时间点，增长按中性处理"
    status = "observed" if trend.confidence_score >= 0.6 else "low_confidence"
    reason = (
        f"基于 {trend.sample_size} 条商品快照、约 {trend.span_days:.0f} 天，"
        f"趋势置信度为{trend.confidence}；增长分已向中性值收缩"
    )
    return trend.growth_score, status, reason


def _improvement_signal(monthly_value: Any, rating_value: Any, demand_score: float) -> tuple[float, str, str]:
    monthly = _number(monthly_value)
    rating = _number(rating_value)
    if monthly is None or rating is None:
        return 50.0, "neutral_missing", "需求或评分缺失，无法判断评分改良空间，按中性处理"
    rating_gap = _clamp((4.7 - rating) / 1.0 * 100.0)
    score = demand_score * 0.55 + rating_gap * 0.45
    return (
        _clamp(score),
        "observed",
        f"需求下界与评分缺口的组合代理；评分 {rating:.1f}，不等同于已验证评论痛点",
    )


def _review_barrier(value: Any) -> tuple[float, str, str]:
    reviews = _number(value)
    if reviews is None:
        return 50.0, "neutral_missing", "评论数缺失，评论壁垒按中性 50 处理"
    if reviews <= 20:
        score = 10.0
    elif reviews >= 10_000:
        score = 95.0
    else:
        ratio = math.log10(reviews / 20.0) / math.log10(10_000.0 / 20.0)
        score = 10.0 + ratio * 85.0
    return _clamp(score), "observed", f"评论数约 {reviews:,.0f}，数值越高代表新品信任壁垒越强"


def _incumbent_pressure(
    monthly_value: Any,
    rank_value: Any,
    rank_usable: bool,
    demand_score: float,
    visibility_score: float,
) -> tuple[float, str, str]:
    monthly = _number(monthly_value)
    rank = _number(rank_value) if rank_usable else None
    if monthly is None and rank is None:
        return 50.0, "neutral_missing", "需求下界和可信自然序位都缺失，头部占位压力按中性处理"
    available = []
    weights = []
    if monthly is not None:
        available.append(demand_score)
        weights.append(0.65)
    if rank is not None:
        available.append(visibility_score)
        weights.append(0.35)
    score = sum(value * weight for value, weight in zip(available, weights)) / sum(weights)
    reason = "高需求或靠前自然序位意味着该商品可能已形成头部占位，不能直接当成低竞争机会"
    return _clamp(score), "observed", reason


def _quality_risk(rating_value: Any, review_value: Any) -> tuple[float, str, str]:
    rating = _number(rating_value)
    reviews = _number(review_value)
    if rating is None:
        return 50.0, "neutral_missing", "评分缺失，质量/退货风险按中性 50 处理"
    if rating >= 4.5:
        raw = 10.0
    elif rating >= 4.2:
        raw = 10.0 + (4.5 - rating) / 0.3 * 20.0
    elif rating >= 4.0:
        raw = 30.0 + (4.2 - rating) / 0.2 * 20.0
    elif rating >= 3.7:
        raw = 50.0 + (4.0 - rating) / 0.3 * 25.0
    else:
        raw = min(95.0, 75.0 + (3.7 - rating) / 0.7 * 20.0)
    reliability = _rating_reliability(reviews)
    score = 50.0 + (raw - 50.0) * reliability
    status = "observed" if reliability >= 0.65 else "low_confidence"
    return _clamp(score), status, f"评分 {rating:.1f} 的风险按评论样本量收缩；仍需评论或退货证据验证"


def _price_risk(value: Any) -> tuple[float, str, str]:
    price = _number(value)
    if price is None or price <= 0:
        return 50.0, "neutral_missing", "价格缺失，价格与利润容错风险按中性 50 处理"
    if price < 8:
        score = 90.0
    elif price < 12:
        score = 70.0
    elif price < 15:
        score = 45.0
    elif price <= 60:
        score = 20.0
    elif price <= 80:
        score = 35.0
    elif price <= 150:
        score = 65.0
    else:
        score = 80.0
    return score, "heuristic", f"价格 ${price:,.2f}；该风险尚未按类目、FBA 和采购成本校准"


def _promo_risk(is_deal_value: Any, trend: TrendAssessment) -> tuple[float, str, str]:
    is_deal = bool(is_deal_value)
    if trend.promo_warning:
        return 85.0, "observed", "促销期间需求或位次明显改善，存在短期拉升风险"
    if is_deal and trend.growth_score > 55:
        return 65.0, "observed", "最新快照处于促销且增长分偏正，需要观察促销结束后的回落"
    if is_deal:
        return 45.0, "observed", "最新快照处于促销，但当前没有足够证据判断是否依赖促销"
    return 15.0, "observed", "最新快照未观察到促销标记"


def _weighted_components(
    raw: Mapping[str, tuple[str, float, str, str, dict[str, Any]]],
    weights: Mapping[str, float],
) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for key, weight in weights.items():
        label, score, status, reason, evidence = raw[key]
        normalized_score = round(_clamp(float(score)), 2)
        components.append(
            {
                "key": key,
                "label": label,
                "score": normalized_score,
                "weight": round(float(weight), 4),
                "contribution": round(normalized_score * float(weight), 2),
                "status": status,
                "reason": reason,
                "evidence": _normalize_value(evidence),
            }
        )
    return components


def _confidence_components(
    row: Mapping[str, Any],
    trend: TrendAssessment,
    *,
    trend_signal_count: int,
    rank_usable: bool,
    as_of: datetime,
) -> list[dict[str, Any]]:
    field_weights = {
        "monthly_bought": 0.30,
        "rating": 0.20,
        "review_count": 0.15,
        "price": 0.15,
        "organic_rank": 0.20,
    }
    completeness = sum(weight for key, weight in field_weights.items() if row.get(key) is not None) * 100.0
    trend_evidence = trend.confidence_score * 100.0 if trend_signal_count >= 2 else 0.0
    if rank_usable:
        rank_reliability = 100.0
        rank_reason = "排名带有 page_first 或 batch_continuous 明确置信标记"
    elif row.get("organic_rank") is not None:
        rank_reliability = 35.0
        rank_reason = "存在自然序位值，但缺少可用排名置信标记"
    else:
        rank_reliability = 0.0
        rank_reason = "没有可用自然序位证据"
    freshness = _freshness_score(row.get("snapshot_at"), as_of)
    identity_fields = ("keyword_id", "rank_snapshot_id", "product_snapshot_id", "snapshot_at")
    traceability = sum(1 for key in identity_fields if row.get(key) is not None) / len(identity_fields) * 100.0
    raw = {
        "field_completeness": (
            "核心字段完整度",
            completeness,
            "observed" if completeness >= 80 else "low_confidence",
            f"模型消费的 5 类核心字段加权覆盖 {completeness:.0f}%",
            {key: row.get(key) is not None for key in field_weights},
        ),
        "trend_evidence": (
            "趋势样本证据",
            trend_evidence,
            "observed" if trend_evidence >= 60 else "low_confidence",
            f"{trend.sample_size} 条快照、约 {trend.span_days:.0f} 天；可比较趋势信号时间点 {trend_signal_count}",
            {"sample_size": trend.sample_size, "span_days": trend.span_days, "signal_points": trend_signal_count},
        ),
        "rank_reliability": (
            "排名可靠性",
            rank_reliability,
            "observed" if rank_reliability >= 80 else "low_confidence",
            rank_reason,
            {"rank_confidence": row.get("rank_confidence"), "organic_rank": row.get("organic_rank")},
        ),
        "freshness": (
            "快照新鲜度",
            freshness,
            "observed" if freshness >= 65 else "low_confidence",
            f"当前关键词批次时间为 {_display_value(row.get('snapshot_at')) or '缺失'}",
            {"snapshot_at": _display_value(row.get("snapshot_at")), "as_of": _display_value(as_of)},
        ),
        "traceability": (
            "来源可追溯性",
            traceability,
            "observed" if traceability == 100 else "low_confidence",
            "检查关键词、排名快照、商品快照和采集时间四类来源标识",
            {key: row.get(key) for key in identity_fields},
        ),
    }
    return _weighted_components(
        raw,
        {
            "field_completeness": 0.40,
            "trend_evidence": 0.30,
            "rank_reliability": 0.15,
            "freshness": 0.10,
            "traceability": 0.05,
        },
    )


def _axis_score(components: Sequence[Mapping[str, Any]]) -> float:
    return round(_clamp(sum(float(item.get("contribution") or 0.0) for item in components)), 2)


def _recommendation(
    profile: StrategyProfile,
    *,
    opportunity_score: float,
    risk_score: float,
    confidence_score: float,
    trend: TrendAssessment,
    trend_signal_count: int,
    opportunity_components: Sequence[Mapping[str, Any]],
    risk_components: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    if profile.code == "trend" and (trend_signal_count < 2 or trend.confidence_score < 0.6):
        return "pause", "趋势型策略需要至少中等置信的多时间点证据；当前先补采同关键词快照"
    if confidence_score < 35:
        return "pause", "关键字段、趋势或排名证据不足，当前分数不适合用于强判断"
    if opportunity_score >= 65 and risk_score >= 60:
        return "benchmark_only", "机会信号较强但竞争或经营风险同样偏高，适合作为对标和差异化研究对象"
    if risk_score >= 75:
        return "pause", "当前风险轴过高，除非后续出现明确差异化、利润和合规证据，否则暂缓"
    if opportunity_score >= 72 and risk_score <= 45 and confidence_score >= 60:
        return "priority_validate", "机会信号、风险与证据质量同时达到影子模型阈值，建议进入人工验证而非直接立项"
    if opportunity_score >= 60 and risk_score <= 60 and confidence_score >= 45:
        return "observe", "当前信号值得继续积累快照，并补充评论痛点、利润和供应链验证"
    if opportunity_score >= 62:
        return "benchmark_only", "存在可研究信号，但风险或证据质量尚不支持进入验证池"
    strongest_opportunity = max(opportunity_components, key=lambda item: float(item.get("contribution") or 0.0))
    strongest_risk = max(risk_components, key=lambda item: float(item.get("contribution") or 0.0))
    return (
        "pause",
        f"机会信号暂不突出；当前主要机会来自{strongest_opportunity['label']}，主要风险来自{strongest_risk['label']}",
    )


def _top_component_reasons(components: Sequence[Mapping[str, Any]], *, reverse: bool) -> list[str]:
    ordered = sorted(components, key=lambda item: float(item.get("contribution") or 0.0), reverse=reverse)
    return [f"{item['label']} {float(item['score']):.0f}：{item['reason']}" for item in ordered]


def _trend_to_dict(trend: TrendAssessment, signal_count: int) -> dict[str, Any]:
    payload = asdict(trend)
    payload["signal_point_count"] = signal_count
    payload["rank_context"] = "同一商品 + 同一关键词"
    return _normalize_value(payload)


def _calibration_context_key(row: Mapping[str, Any]) -> str:
    keyword_id = int(row.get("keyword_id") or 0)
    product_id = int(row.get("product_id") or 0)
    if keyword_id or product_id:
        return f"{keyword_id}:{product_id}"
    return f"{str(row.get('keyword') or '')}:{str(row.get('asin') or '')}"


def _calibration_confidence_bucket(value: Any) -> str:
    score = _number(value) or 0.0
    if score >= 75:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _calibration_flags(
    row: Mapping[str, Any],
    strategy_outcomes: Mapping[str, Mapping[str, Any]],
    *,
    asin_context_count: int,
) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []

    def add(code: str, reason: str) -> None:
        label, severity, _priority = _CALIBRATION_FLAG_META[code]
        flags.append({"code": code, "label": label, "severity": severity, "reason": reason})

    recommendations = {
        str(outcome.get("recommendation_code") or "")
        for outcome in strategy_outcomes.values()
        if outcome.get("recommendation_code")
    }
    if len(recommendations) > 1:
        labels = sorted(
            {
                str(outcome.get("recommendation_label") or outcome.get("recommendation_code") or "")
                for outcome in strategy_outcomes.values()
            }
        )
        add("strategy_disagreement", f"四种研究策略产生 {len(recommendations)} 类建议：{'、'.join(labels)}")

    trend = row.get("trend") if isinstance(row.get("trend"), Mapping) else {}
    signal_points = int(_number(trend.get("signal_point_count")) or 0)
    sample_size = int(_number(trend.get("sample_size")) or 0)
    span_days = _number(trend.get("span_days")) or 0.0
    if signal_points < 2:
        add("single_snapshot", f"只有 {signal_points or sample_size} 个可比较时间点，不能验证增长")
    elif span_days < 28:
        add("short_trend", f"趋势跨度约 {span_days:.0f} 天，尚不足以验证月度稳定性或季节性")

    opportunity = _number(row.get("opportunity_score")) or 0.0
    risk = _number(row.get("risk_score")) or 0.0
    confidence = _number(row.get("confidence_score")) or 0.0
    if opportunity >= 70 and confidence < 55:
        add("low_confidence_high_opportunity", "机会分不低，但置信度不足 55，应优先核对证据而不是看结论")
    if opportunity >= 70 and risk >= 60:
        add("high_opportunity_high_risk", "机会与风险同时偏高，重点复核是否仅适合作为头部对标")

    if _number(row.get("price")) is not None:
        add("price_uncalibrated", "价格风险仍使用全局启发式，未按类目、尺寸、FBA 和采购成本校准")

    rank_confidence = str(row.get("rank_confidence") or "unknown")
    if row.get("organic_rank") is None or rank_confidence not in _RANK_USABLE:
        add("rank_evidence_gap", f"自然序位或排名置信不足；当前标记为 {rank_confidence}")

    field_labels = {
        "monthly_bought": "近月购买量",
        "rating": "评分",
        "review_count": "评论数",
        "price": "价格",
        "organic_rank": "自然序位",
    }
    missing = [label for key, label in field_labels.items() if row.get(key) is None]
    if missing:
        add("missing_core_fields", f"缺少核心字段：{'、'.join(missing)}")

    if asin_context_count > 1:
        add("multi_keyword_context", f"该 ASIN 在当前回放中有 {asin_context_count} 个关键词上下文，需按关键词分别判断")

    boundary_distances = [
        *(abs(opportunity - threshold) for threshold in (60, 62, 65, 72)),
        *(abs(risk - threshold) for threshold in (45, 60, 75)),
        *(abs(confidence - threshold) for threshold in (35, 45, 55, 60, 75)),
    ]
    if boundary_distances and min(boundary_distances) <= 2:
        add("threshold_boundary", "至少一个轴接近当前建议阈值，轻微权重变化可能改变结论")

    if trend.get("promo_warning"):
        add("promotion_sensitive", str(trend.get("promo_warning")))
    return flags


def _select_calibration_samples(
    rows: Sequence[dict[str, Any]],
    sample_per_bucket: int,
    *,
    sample_seed: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("sample_bucket") or "unknown:low"), []).append(row)

    selected: list[dict[str, Any]] = []
    used_asins: set[str] = set()
    bucket_summary: list[dict[str, Any]] = []
    for recommendation_code in _RECOMMENDATION_LABELS:
        for confidence_bucket in _CONFIDENCE_BUCKETS:
            bucket_key = f"{recommendation_code}:{confidence_bucket}"
            candidates = grouped.get(bucket_key, [])
            if not candidates:
                continue
            by_asin: dict[str, dict[str, Any]] = {}
            ordered_for_dedupe = sorted(
                candidates,
                key=lambda row: (
                    -int(row.get("calibration_priority") or 0),
                    -float(row.get("opportunity_score") or 0.0),
                    str(row.get("context_key") or ""),
                ),
            )
            for row in ordered_for_dedupe:
                asin = str(row.get("asin") or row.get("context_key") or "")
                by_asin.setdefault(asin, row)
            unique_candidates = sorted(
                by_asin.values(),
                key=lambda row: (
                    float(row.get("opportunity_score") or 0.0),
                    float(row.get("risk_score") or 0.0),
                    str(row.get("context_key") or ""),
                ),
            )
            fresh_pool = [row for row in unique_candidates if str(row.get("asin") or "") not in used_asins]
            picked = _pick_seeded_quantile_rows(
                fresh_pool,
                sample_per_bucket,
                sample_seed=sample_seed,
                bucket_key=bucket_key,
            )
            if len(picked) < sample_per_bucket:
                picked_keys = {str(row.get("context_key") or "") for row in picked}
                fallback = [
                    row
                    for row in unique_candidates
                    if str(row.get("context_key") or "") not in picked_keys
                ]
                picked.extend(
                    _pick_seeded_quantile_rows(
                        fallback,
                        sample_per_bucket - len(picked),
                        sample_seed=sample_seed,
                        bucket_key=f"{bucket_key}:fallback",
                    )
                )
            for row in picked:
                used_asins.add(str(row.get("asin") or ""))
                row["sample_rank"] = len(selected) + 1
                selected.append(row)
            bucket_summary.append(
                {
                    "key": bucket_key,
                    "label": f"{_RECOMMENDATION_LABELS[recommendation_code]} · {_CONFIDENCE_BUCKETS[confidence_bucket]}",
                    "recommendation_code": recommendation_code,
                    "confidence_bucket": confidence_bucket,
                    "context_count": len(candidates),
                    "unique_asin_count": len(unique_candidates),
                    "sample_count": len(picked),
                }
            )
    return selected, bucket_summary


def _pick_seeded_quantile_rows(
    rows: Sequence[dict[str, Any]],
    count: int,
    *,
    sample_seed: str,
    bucket_key: str,
) -> list[dict[str, Any]]:
    if count <= 0 or not rows:
        return []
    if sample_seed == "baseline":
        return _pick_legacy_quantile_rows(rows, count)
    if len(rows) <= count:
        return sorted(rows, key=lambda row: _calibration_sample_hash(sample_seed, bucket_key, row))

    picked: list[dict[str, Any]] = []
    total = len(rows)
    for slot in range(count):
        start = slot * total // count
        end = (slot + 1) * total // count
        band = rows[start:end] or rows[start : start + 1]
        picked.append(
            min(
                band,
                key=lambda row: _calibration_sample_hash(
                    sample_seed,
                    f"{bucket_key}:{slot}",
                    row,
                ),
            )
        )
    return picked


def _pick_legacy_quantile_rows(rows: Sequence[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if count <= 0 or not rows:
        return []
    if len(rows) <= count:
        return list(rows)
    if count == 1:
        return [rows[len(rows) // 2]]
    indexes = [round(index * (len(rows) - 1) / (count - 1)) for index in range(count)]
    picked: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index in indexes:
        if index not in seen:
            seen.add(index)
            picked.append(rows[index])
    if len(picked) < count:
        for index, row in enumerate(rows):
            if index in seen:
                continue
            picked.append(row)
            if len(picked) >= count:
                break
    return picked


def _calibration_sample_hash(sample_seed: str, bucket_key: str, row: Mapping[str, Any]) -> str:
    identity = str(row.get("context_key") or row.get("sample_key") or row.get("asin") or "")
    return hashlib.sha256(f"{sample_seed}|{bucket_key}|{identity}".encode("utf-8")).hexdigest()


def _normalize_calibration_seed(value: Any) -> str:
    seed = str(value or "baseline").strip() or "baseline"
    if len(seed) > 64:
        raise ScoringV2Error("校准抽样种子不能超过 64 个字符")
    return seed


def _normalize_calibration_excluded_asins(values: Iterable[str]) -> set[str]:
    raw_values = [values] if isinstance(values, str) else list(values or ())
    if len(raw_values) > MAX_CALIBRATION_EXCLUDED_ASINS:
        raise ScoringV2Error(f"单次最多排除 {MAX_CALIBRATION_EXCLUDED_ASINS} 个已复核 ASIN")
    result: set[str] = set()
    for raw in raw_values:
        asin = str(raw or "").strip().upper()
        if not asin:
            continue
        if len(asin) != 10 or not asin.isalnum():
            raise ScoringV2Error(f"无效的排除 ASIN：{asin}")
        result.add(asin)
    return result


def _build_replay_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    recommendation_counts = {label: 0 for label in _RECOMMENDATION_LABELS.values()}
    confidence_counts = {"高": 0, "中": 0, "低": 0, "证据不足": 0}
    for row in rows:
        recommendation_counts[str(row.get("recommendation_label"))] = (
            recommendation_counts.get(str(row.get("recommendation_label")), 0) + 1
        )
        confidence_counts[str(row.get("confidence_level"))] = confidence_counts.get(str(row.get("confidence_level")), 0) + 1
    legacy = [float(row["legacy_total_score"]) for row in rows if row.get("legacy_total_score") is not None]
    opportunities = [float(row["opportunity_score"]) for row in rows]
    risks = [float(row["risk_score"]) for row in rows]
    confidences = [float(row["confidence_score"]) for row in rows]
    return {
        "context_count": total,
        "product_count": len({row.get("product_id") for row in rows if row.get("product_id") is not None}),
        "keyword_count": len({row.get("keyword_id") for row in rows if row.get("keyword_id") is not None}),
        "high_opportunity_count": sum(1 for row in rows if float(row.get("opportunity_score") or 0.0) >= 70),
        "high_risk_count": sum(1 for row in rows if float(row.get("risk_score") or 0.0) >= 60),
        "low_confidence_count": sum(1 for row in rows if float(row.get("confidence_score") or 0.0) < 55),
        "recommendation_counts": recommendation_counts,
        "confidence_counts": confidence_counts,
        "averages": {
            "opportunity": _average(opportunities),
            "risk": _average(risks),
            "confidence": _average(confidences),
            "legacy_total": _average(legacy),
        },
        "medians": {
            "opportunity": _median(opportunities),
            "risk": _median(risks),
            "confidence": _median(confidences),
        },
        "coverage": {
            "monthly_bought": _coverage(rows, lambda row: row.get("monthly_bought") is not None),
            "usable_rank": _coverage(rows, lambda row: row.get("rank_confidence") in _RANK_USABLE),
            "trend_medium_or_high": _coverage(
                rows,
                lambda row: float((row.get("trend") or {}).get("confidence_score") or 0.0) >= 0.6,
            ),
            "legacy_score": round(len(legacy) / total, 4) if total else 0.0,
        },
    }


def _sort_rows(rows: Sequence[dict[str, Any]], sort_by: str, direction: str) -> list[dict[str, Any]]:
    present = [row for row in rows if row.get(sort_by) is not None]
    missing = [row for row in rows if row.get(sort_by) is None]
    present.sort(key=lambda row: str(row.get("asin") or ""))
    present.sort(key=lambda row: float(row.get("opportunity_score") or 0.0), reverse=True)
    present.sort(key=lambda row: float(row.get("confidence_score") or 0.0), reverse=True)
    present.sort(key=lambda row: row.get(sort_by), reverse=direction == "desc")
    missing.sort(key=lambda row: (-(float(row.get("confidence_score") or 0.0)), str(row.get("asin") or "")))
    return present + missing


def _strategy(code: str | None) -> StrategyProfile:
    normalized = str(code or "balanced").strip().lower()
    profile = STRATEGIES.get(normalized)
    if profile is None:
        raise ScoringV2Error(f"未知评分策略：{normalized}；可选 {', '.join(STRATEGIES)}")
    return profile


def _normalize_recommendation(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or normalized == "all":
        return None
    if normalized not in _RECOMMENDATION_LABELS:
        raise ScoringV2Error(f"未知建议状态：{normalized}")
    return normalized


def _optional_score(value: Any, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return _clamp(float(value))
    except (TypeError, ValueError) as exc:
        raise ScoringV2Error(f"{label}必须是 0-100 的数字") from exc


def _trend_signal_count(rows: Iterable[Mapping[str, Any]]) -> int:
    monthly_times = {_time_key(row.get("snapshot_at")) for row in rows if _number(row.get("monthly_bought")) is not None}
    rank_times = {_time_key(row.get("snapshot_at")) for row in rows if _number(row.get("organic_rank")) is not None}
    return max(len(monthly_times - {""}), len(rank_times - {""}))


def _rating_reliability(review_value: Any) -> float:
    reviews = _number(review_value)
    if reviews is None or reviews <= 0:
        return 0.0
    return _clamp(math.log10(reviews + 1.0) / math.log10(201.0), 0.0, 1.0)


def _freshness_score(value: Any, as_of: datetime) -> float:
    snapshot = _as_datetime(value)
    if snapshot is None:
        return 0.0
    age_days = max(0.0, (as_of - snapshot).total_seconds() / 86400.0)
    if age_days <= 2:
        return 100.0
    if age_days <= 7:
        return 85.0
    if age_days <= 14:
        return 65.0
    if age_days <= 30:
        return 40.0
    if age_days <= 60:
        return 20.0
    return 10.0


def _confidence_level(score: float) -> str:
    if score >= 75:
        return "高"
    if score >= 55:
        return "中"
    if score >= 35:
        return "低"
    return "证据不足"


def _coverage(rows: Sequence[Mapping[str, Any]], predicate: Any) -> float:
    return round(sum(1 for row in rows if predicate(row)) / len(rows), 4) if rows else 0.0


def _average(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _median(values: Sequence[float]) -> float | None:
    return round(float(statistics.median(values)), 2) if values else None


def _chunks(values: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_value(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value
    return value


def _display_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _time_key(value: Any) -> str:
    parsed = _as_datetime(value)
    return parsed.isoformat(sep=" ", timespec="seconds") if parsed else ""


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))
