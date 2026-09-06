"""Versioned seller-domain scoring profiles and read-only product evaluation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from database.mysql_client import MySQLClient
from services.scoring_v2 import (
    MODEL_VERSION,
    STRATEGIES,
    ScoringV2Error,
    StrategyProfile,
    load_product_scoring_evidence,
    load_product_scoring_evidence_batch,
    score_shadow_context,
)


PROFILE_CONFIG_SCHEMA_VERSION = "domain-scoring-profile-v1"
PROFILE_STATUSES = {"draft", "active", "archived"}
PROFILE_STATUS_LABELS = {"draft": "草稿", "active": "已启用", "archived": "已归档"}
PROFILE_SCOPE_TYPES = {"marketplace", "category", "niche", "hybrid"}
PROFILE_SCOPE_LABELS = {
    "marketplace": "站点通用",
    "category": "Amazon 类目",
    "niche": "市场利基",
    "hybrid": "类目 + 利基",
}
OPPORTUNITY_KEYS = ("demand", "growth", "acceptance", "visibility", "improvement_space")
RISK_KEYS = ("review_barrier", "incumbent_pressure", "quality_risk", "price_risk", "promo_risk")
OPPORTUNITY_LABELS = {
    "demand": "需求下界",
    "growth": "趋势增长",
    "acceptance": "评分接受度",
    "visibility": "自然可见度",
    "improvement_space": "改良空间代理",
}
RISK_LABELS = {
    "review_barrier": "评论壁垒",
    "incumbent_pressure": "头部占位压力",
    "quality_risk": "质量风险",
    "price_risk": "价格与利润容错",
    "promo_risk": "促销依赖风险",
}
AXIS_KEYS = ("opportunity", "risk_control")
RECOMMENDATION_POLICY_DEFAULTS = {
    "minimum_confidence": 35.0,
    "high_opportunity": 65.0,
    "high_risk": 60.0,
    "critical_risk": 75.0,
    "priority_opportunity": 72.0,
    "priority_max_risk": 45.0,
    "priority_confidence": 60.0,
    "observe_opportunity": 60.0,
    "observe_max_risk": 60.0,
    "observe_confidence": 45.0,
    "fallback_benchmark_opportunity": 62.0,
}
RECOMMENDATION_POLICY_LABELS = {
    "minimum_confidence": "最低可判断置信度",
    "high_opportunity": "高机会高风险的机会门槛",
    "high_risk": "高机会高风险的风险门槛",
    "critical_risk": "极高风险门槛",
    "priority_opportunity": "优先验证最低机会分",
    "priority_max_risk": "优先验证最高风险分",
    "priority_confidence": "优先验证最低置信度",
    "observe_opportunity": "观察池最低机会分",
    "observe_max_risk": "观察池最高风险分",
    "observe_confidence": "观察池最低置信度",
    "fallback_benchmark_opportunity": "对标研究最低机会分",
}
RECOMMENDATION_LABELS = {
    "priority_validate": "优先人工验证",
    "observe": "可进入观察池",
    "benchmark_only": "仅适合作为对标",
    "pause": "暂缓",
}
_ALLOWED_CONFIG_KEYS = {
    "base_strategy",
    "normalization_mode",
    "opportunity_weights",
    "risk_weights",
    "axis_blend",
    "recommendation_policy",
    "minimum_applicability",
    "trend_priority_guard",
}
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


class DomainScoringError(ValueError):
    """Raised for invalid profile configuration, lifecycle, or applicability."""


def get_domain_scoring_catalog() -> dict[str, Any]:
    return {
        "config_schema_version": PROFILE_CONFIG_SCHEMA_VERSION,
        "parent_model_version": MODEL_VERSION,
        "statuses": PROFILE_STATUS_LABELS,
        "scope_types": PROFILE_SCOPE_LABELS,
        "base_strategies": [profile.to_dict() for profile in STRATEGIES.values()],
        "opportunity_components": OPPORTUNITY_LABELS,
        "risk_components": RISK_LABELS,
        "axis_components": {"opportunity": "机会轴", "risk_control": "风险控制轴"},
        "recommendation_policy": RECOMMENDATION_POLICY_LABELS,
        "default_config": build_default_profile_config("balanced"),
        "boundaries": [
            "领域模型学习并表达卖家判断偏好，不等同于利润或爆款预测。",
            "V1 使用评分 V2 的通用信号曲线；类目百分位标准化仍需更多类目证据。",
            "试算不写 product_scores，也不改变商品池、推荐或关键词机会排序。",
            "自动训练、批量接管和生产切换当前冻结。",
        ],
    }


def build_default_profile_config(base_strategy: str = "balanced") -> dict[str, Any]:
    strategy = _base_strategy(base_strategy)
    return {
        "base_strategy": strategy.code,
        "normalization_mode": "v2_global_signals",
        "opportunity_weights": dict(strategy.opportunity_weights),
        "risk_weights": dict(strategy.risk_weights),
        "axis_blend": {"opportunity": 0.60, "risk_control": 0.40},
        "recommendation_policy": dict(RECOMMENDATION_POLICY_DEFAULTS),
        "minimum_applicability": 60.0,
        "trend_priority_guard": strategy.code == "trend",
    }


def validate_profile_config(
    config: Mapping[str, Any] | None,
    *,
    base_strategy: str | None = None,
) -> dict[str, Any]:
    raw = dict(config or {})
    unknown = sorted(set(raw) - _ALLOWED_CONFIG_KEYS)
    if unknown:
        raise DomainScoringError(f"领域模型包含未登记参数：{'、'.join(unknown)}")
    strategy_code = str(raw.get("base_strategy") or base_strategy or "balanced").strip()
    defaults = build_default_profile_config(strategy_code)
    normalized = dict(defaults)
    normalized.update({key: value for key, value in raw.items() if key not in {
        "opportunity_weights", "risk_weights", "axis_blend", "recommendation_policy"
    }})
    normalized["base_strategy"] = strategy_code
    normalized["opportunity_weights"] = _weight_map(
        {**defaults["opportunity_weights"], **dict(raw.get("opportunity_weights") or {})},
        OPPORTUNITY_KEYS,
        "机会轴权重",
    )
    normalized["risk_weights"] = _weight_map(
        {**defaults["risk_weights"], **dict(raw.get("risk_weights") or {})},
        RISK_KEYS,
        "风险轴权重",
    )
    normalized["axis_blend"] = _weight_map(
        {**defaults["axis_blend"], **dict(raw.get("axis_blend") or {})},
        AXIS_KEYS,
        "专项分轴组合",
    )
    policy_raw = {**RECOMMENDATION_POLICY_DEFAULTS, **dict(raw.get("recommendation_policy") or {})}
    unknown_policy = sorted(set(policy_raw) - set(RECOMMENDATION_POLICY_DEFAULTS))
    if unknown_policy:
        raise DomainScoringError(f"建议阈值包含未登记参数：{'、'.join(unknown_policy)}")
    policy = {
        key: round(_score(value, RECOMMENDATION_POLICY_LABELS[key]), 2)
        for key, value in policy_raw.items()
    }
    _validate_recommendation_policy(policy)
    normalized["recommendation_policy"] = policy
    normalization_mode = str(normalized.get("normalization_mode") or "").strip()
    if normalization_mode != "v2_global_signals":
        raise DomainScoringError("V1 仅支持 v2_global_signals 通用信号标准化")
    normalized["normalization_mode"] = normalization_mode
    normalized["minimum_applicability"] = round(
        _score(normalized.get("minimum_applicability"), "最低模型适用度"),
        2,
    )
    normalized["trend_priority_guard"] = bool(normalized.get("trend_priority_guard"))
    return normalized


def fetch_domain_profiles_page(
    *,
    marketplace: str = "US",
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    marketplace_value = _marketplace(marketplace)
    limit_value = _bounded_int(limit, 100, 1, 200)
    offset_value = _bounded_int(offset, 0, 0, 10_000_000)
    where = ["sp.marketplace = %s"]
    params: list[Any] = [marketplace_value]
    if status and status != "all":
        where.append("sp.status = %s")
        params.append(_choice(status, PROFILE_STATUSES, "模型状态"))
    where_sql = " AND ".join(where)
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS total FROM scoring_profiles sp WHERE {where_sql}", params)
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                f"""
                {_profile_select()}
                WHERE {where_sql}
                ORDER BY sp.updated_at DESC, sp.id DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit_value, offset_value],
            )
            rows = [_profile_from_row(row) for row in cursor.fetchall()]
    return {
        "rows": rows,
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "marketplace": marketplace_value,
        "writes_production_score": False,
    }


def get_domain_profile(profile_id: int, *, client: MySQLClient | None = None) -> dict[str, Any]:
    db = client or MySQLClient()
    profile_id_value = _positive_int(profile_id, "领域模型 ID")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"{_profile_select()} WHERE sp.id = %s LIMIT 1", (profile_id_value,))
            row = cursor.fetchone()
            if not row:
                raise DomainScoringError(f"未找到领域模型 #{profile_id_value}")
            cursor.execute(
                """
                SELECT spv.*, mn.name AS niche_name
                FROM scoring_profile_versions spv
                LEFT JOIN market_niches mn ON mn.id = spv.niche_id
                WHERE spv.profile_id = %s
                ORDER BY spv.version_no DESC
                """,
                (profile_id_value,),
            )
            versions = [_version_from_row(item) for item in cursor.fetchall()]
    profile = _profile_from_row(row)
    return {"profile": profile, "versions": versions, "writes_production_score": False}


def create_domain_profile(
    name: str,
    *,
    marketplace: str = "US",
    description: str | None = None,
    scope_type: str = "marketplace",
    category_scope: str | None = None,
    niche_id: int | None = None,
    base_strategy: str = "balanced",
    config: Mapping[str, Any] | None = None,
    change_note: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    name_value = _required_text(name, "模型名称", 128)
    normalized_name = _normalize_name(name_value)
    marketplace_value = _marketplace(marketplace)
    description_value = _optional_text(description, 20_000)
    scope = _scope_values(scope_type, category_scope, niche_id)
    config_value = validate_profile_config(config, base_strategy=base_strategy)
    config_hash = _profile_config_hash(scope, config_value)
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                _validate_scope_reference(cursor, marketplace_value, scope)
                cursor.execute(
                    """
                    INSERT INTO scoring_profiles (
                      marketplace, name, normalized_name, description, status
                    ) VALUES (%s, %s, %s, %s, 'draft')
                    """,
                    (marketplace_value, name_value, normalized_name, description_value),
                )
                profile_id = int(cursor.lastrowid)
                version_id = _insert_version(
                    cursor,
                    profile_id=profile_id,
                    version_no=1,
                    scope=scope,
                    config=config_value,
                    config_hash=config_hash,
                    change_note=_optional_text(change_note, 1000) or "创建领域模型",
                )
                cursor.execute(
                    "UPDATE scoring_profiles SET current_version_id = %s WHERE id = %s",
                    (version_id, profile_id),
                )
    except db._pymysql.err.IntegrityError as exc:
        raise DomainScoringError(f"{marketplace_value} 站点已存在同名模型或相同参数版本") from exc
    return get_domain_profile(profile_id, client=db)


def update_domain_profile(
    profile_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    profile_id_value = _positive_int(profile_id, "领域模型 ID")
    assignments: list[str] = []
    params: list[Any] = []
    if name is not None:
        name_value = _required_text(name, "模型名称", 128)
        assignments.extend(["name = %s", "normalized_name = %s"])
        params.extend([name_value, _normalize_name(name_value)])
    if description is not None:
        assignments.append("description = %s")
        params.append(_optional_text(description, 20_000))
    if not assignments:
        return get_domain_profile(profile_id_value, client=db)
    assignments.append("updated_at = CURRENT_TIMESTAMP")
    params.append(profile_id_value)
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                _require_profile(cursor, profile_id_value)
                cursor.execute(
                    f"UPDATE scoring_profiles SET {', '.join(assignments)} WHERE id = %s",
                    params,
                )
    except db._pymysql.err.IntegrityError as exc:
        raise DomainScoringError("同站点已存在同名领域模型") from exc
    return get_domain_profile(profile_id_value, client=db)


def create_domain_profile_version(
    profile_id: int,
    *,
    scope_type: str,
    category_scope: str | None = None,
    niche_id: int | None = None,
    config: Mapping[str, Any],
    change_note: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    profile_id_value = _positive_int(profile_id, "领域模型 ID")
    scope = _scope_values(scope_type, category_scope, niche_id)
    config_value = validate_profile_config(config)
    config_hash = _profile_config_hash(scope, config_value)
    try:
        with db.connect() as conn:
            with conn.cursor() as cursor:
                profile = _require_profile(cursor, profile_id_value, for_update=True)
                if profile["status"] == "archived":
                    raise DomainScoringError("已归档模型不能新增版本；请先转为草稿")
                _validate_scope_reference(cursor, str(profile["marketplace"]), scope)
                cursor.execute(
                    "SELECT COALESCE(MAX(version_no), 0) + 1 AS next_no FROM scoring_profile_versions WHERE profile_id = %s",
                    (profile_id_value,),
                )
                version_no = int((cursor.fetchone() or {}).get("next_no") or 1)
                version_id = _insert_version(
                    cursor,
                    profile_id=profile_id_value,
                    version_no=version_no,
                    scope=scope,
                    config=config_value,
                    config_hash=config_hash,
                    change_note=_optional_text(change_note, 1000),
                )
                cursor.execute(
                    "UPDATE scoring_profiles SET current_version_id = %s, status = 'draft', updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (version_id, profile_id_value),
                )
    except db._pymysql.err.IntegrityError as exc:
        raise DomainScoringError("参数和适用范围与该模型的已有版本完全相同，无需重复保存") from exc
    return get_domain_profile(profile_id_value, client=db)


def set_domain_profile_status(
    profile_id: int,
    status: str,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    profile_id_value = _positive_int(profile_id, "领域模型 ID")
    status_value = _choice(status, PROFILE_STATUSES, "模型状态")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            profile = _require_profile(cursor, profile_id_value, for_update=True)
            if status_value == "active" and not profile.get("current_version_id"):
                raise DomainScoringError("模型没有可用参数版本，不能启用")
            cursor.execute(
                "UPDATE scoring_profiles SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (status_value, profile_id_value),
            )
    return get_domain_profile(profile_id_value, client=db)


def set_domain_profile_current_version(
    profile_id: int,
    version_id: int,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    profile_id_value = _positive_int(profile_id, "领域模型 ID")
    version_id_value = _positive_int(version_id, "模型版本 ID")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            profile = _require_profile(cursor, profile_id_value, for_update=True)
            if profile["status"] == "archived":
                raise DomainScoringError("已归档模型不能切换版本；请先转为草稿")
            cursor.execute(
                "SELECT id FROM scoring_profile_versions WHERE id = %s AND profile_id = %s LIMIT 1",
                (version_id_value, profile_id_value),
            )
            if not cursor.fetchone():
                raise DomainScoringError(f"领域模型 #{profile_id_value} 不包含版本 #{version_id_value}")
            cursor.execute(
                "UPDATE scoring_profiles SET current_version_id = %s, status = 'draft', updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (version_id_value, profile_id_value),
            )
    return get_domain_profile(profile_id_value, client=db)


def evaluate_product_with_domain_profile(
    profile_id: int,
    asin: str,
    *,
    keyword: str | None = None,
    version_id: int | None = None,
    client: MySQLClient | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    asin_value = str(asin or "").strip().upper()
    if not _ASIN_RE.fullmatch(asin_value):
        raise DomainScoringError("ASIN 必须是 10 位字母或数字")
    profile, version = _resolve_profile_version(profile_id, version_id, db)
    evidence = load_product_scoring_evidence(
        asin_value,
        marketplace=profile["marketplace"],
        keyword=keyword,
        client=db,
    )
    return _evaluate_loaded_domain_evidence(
        profile,
        version,
        evidence,
        db,
        as_of=as_of,
    )


def evaluate_domain_profile_batch(
    profile_id: int,
    *,
    version_id: int | None = None,
    search: str = "",
    evidence_scope: str = "all",
    signal: str = "all",
    min_abs_delta: float = 0.0,
    sort_by: str = "abs_delta",
    sort_dir: str = "desc",
    sample_limit: int = 50,
    limit: int = 20,
    offset: int = 0,
    client: MySQLClient | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build a deterministic, read-only validation sample for one profile version."""

    db = client or MySQLClient()
    profile, version = _resolve_profile_version(profile_id, version_id, db)
    evidence_scope_value = _choice(
        evidence_scope,
        {"all", "product_keyword", "product_only"},
        "证据上下文",
    )
    signal_value = _choice(
        signal,
        {"all", "changed", "low_confidence", "scope_review"},
        "验证信号",
    )
    sort_by_value = _choice(
        sort_by,
        {
            "abs_delta",
            "specialized_score",
            "confidence",
            "applicability",
            "opportunity",
            "risk",
            "snapshot_at",
        },
        "排序字段",
    )
    sort_dir_value = _choice(sort_dir, {"asc", "desc"}, "排序方向")
    try:
        min_delta_value = float(min_abs_delta)
    except (TypeError, ValueError) as exc:
        raise DomainScoringError("最低分差必须是 0-100 的数字") from exc
    if not 0.0 <= min_delta_value <= 100.0:
        raise DomainScoringError("最低分差必须在 0-100 之间")
    sample_limit_value = _bounded_int(sample_limit, 50, 1, 50)
    limit_value = _bounded_int(limit, 20, 1, 100)
    offset_value = _bounded_int(offset, 0, 0, 10_000_000)
    search_value = " ".join(str(search or "").strip().split())[:255]

    candidate_payload = _load_domain_validation_candidates(
        db,
        profile,
        version,
        search=search_value,
        sample_limit=sample_limit_value,
    )
    sample = candidate_payload["sample"]
    preferred_keywords = {
        row["asin"]: row["preferred_keyword"]
        for row in sample
        if row.get("preferred_keyword")
    }
    evidence_by_asin = load_product_scoring_evidence_batch(
        [row["asin"] for row in sample],
        marketplace=profile["marketplace"],
        preferred_keywords=preferred_keywords,
        client=db,
    )
    calculated_at = as_of or datetime.now()
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for candidate in sample:
        asin_value = candidate["asin"]
        evidence = evidence_by_asin.get(asin_value)
        if not evidence:
            failures.append({"asin": asin_value, "reason": "未找到可计算的商品快照或指定关键词上下文"})
            continue
        context = evidence["context"]
        keyword_id = int(context["keyword_id"]) if context.get("keyword_id") is not None else None
        applicability = _profile_applicability_from_facts(
            version,
            categories=candidate["observed_categories"],
            product_member=bool(candidate.get("niche_product_member")),
            keyword_member=keyword_id in set(candidate.get("niche_keyword_ids") or []),
            keyword_id=keyword_id,
        )
        try:
            evaluated = _evaluate_loaded_domain_evidence(
                profile,
                version,
                evidence,
                db,
                as_of=calculated_at,
                applicability=applicability,
            )
        except (DomainScoringError, ScoringV2Error) as exc:
            failures.append({"asin": asin_value, "reason": str(exc)})
            continue
        rows.append(_domain_batch_row(candidate, evidence, evaluated))

    confidence_floor = 55.0
    filtered = [row for row in rows if float(row["abs_delta"]) >= min_delta_value]
    if evidence_scope_value != "all":
        filtered = [row for row in filtered if row["context_scope"] == evidence_scope_value]
    if signal_value == "changed":
        filtered = [row for row in filtered if row["flags"]["changed"]]
    elif signal_value == "low_confidence":
        filtered = [row for row in filtered if float(row["confidence_score"]) < confidence_floor]
    elif signal_value == "scope_review":
        filtered = [row for row in filtered if not row["applicability"]["eligible"]]

    sort_values = {
        "abs_delta": lambda row: float(row["abs_delta"]),
        "specialized_score": lambda row: float(row["custom_specialized_score"]),
        "confidence": lambda row: float(row["confidence_score"]),
        "applicability": lambda row: float(row["applicability"]["score"]),
        "opportunity": lambda row: float(row["opportunity_score"]),
        "risk": lambda row: float(row["risk_score"]),
        "snapshot_at": lambda row: str(row.get("snapshot_at") or ""),
    }
    filtered.sort(key=lambda row: str(row.get("asin") or ""))
    filtered.sort(key=sort_values[sort_by_value], reverse=sort_dir_value == "desc")
    total = len(filtered)
    page_rows = filtered[offset_value:offset_value + limit_value]
    context_counts = {
        "product_keyword": sum(1 for row in rows if row["context_scope"] == "product_keyword"),
        "product_only": sum(1 for row in rows if row["context_scope"] == "product_only"),
    }
    return {
        "profile": profile,
        "version": version,
        "rows": page_rows,
        "total": total,
        "limit": limit_value,
        "offset": offset_value,
        "filters": {
            "search": search_value,
            "evidence_scope": evidence_scope_value,
            "signal": signal_value,
            "min_abs_delta": round(min_delta_value, 2),
            "sort_by": sort_by_value,
            "sort_dir": sort_dir_value,
        },
        "candidate_summary": {
            "scope_candidate_total": candidate_payload["scope_candidate_total"],
            "search_candidate_total": candidate_payload["search_candidate_total"],
            "sample_limit": sample_limit_value,
            "sampled_total": len(sample),
            "scored_total": len(rows),
            "failed_total": len(failures),
            "sample_truncated": candidate_payload["search_candidate_total"] > len(sample),
            "context_counts": context_counts,
            "changed_total": sum(1 for row in rows if row["flags"]["changed"]),
            "low_confidence_total": sum(
                1 for row in rows if float(row["confidence_score"]) < confidence_floor
            ),
            "scope_review_total": sum(1 for row in rows if not row["applicability"]["eligible"]),
            "decision_counts": _count_values(row["decision"]["code"] for row in rows),
        },
        "parameter_comparison": _profile_parameter_comparison(version["config"]),
        "failures": failures[:20],
        "generated_at": calculated_at.isoformat(sep=" ", timespec="seconds"),
        "writes_production_score": False,
        "boundaries": [
            "这是最多 50 条的可复现验证样本，不是全库商品排名。",
            "候选只使用已采集类目、BSR 类目或已绑定利基关系，不根据标题猜测类目。",
            "批量试算不写 product_scores，不自动训练，也不改变商品池、推荐或关键词排序。",
        ],
    }


def _resolve_profile_version(
    profile_id: int,
    version_id: int | None,
    db: MySQLClient,
) -> tuple[dict[str, Any], dict[str, Any]]:
    profile_payload = get_domain_profile(profile_id, client=db)
    profile = profile_payload["profile"]
    versions = profile_payload["versions"]
    current = profile.get("current_version") or {}
    selected_version_id = int(version_id) if version_id is not None else int(current.get("id") or 0)
    version = next((item for item in versions if int(item["id"]) == selected_version_id), None)
    if not version:
        raise DomainScoringError(f"领域模型 #{profile_id} 不包含版本 #{selected_version_id}")
    return profile, version


def _evaluate_loaded_domain_evidence(
    profile: Mapping[str, Any],
    version: Mapping[str, Any],
    evidence: Mapping[str, Any],
    db: MySQLClient,
    *,
    as_of: datetime | None = None,
    applicability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = evidence["context"]
    config = version["config"]
    calculated_at = as_of or datetime.now()
    baseline_profile = _base_strategy(config["base_strategy"])
    custom_profile = StrategyProfile(
        code=f"domain-{profile['id']}-v{version['version_no']}",
        label=str(profile["name"]),
        description=profile.get("description") or "用户领域评分模型",
        opportunity_weights=config["opportunity_weights"],
        risk_weights=config["risk_weights"],
    )
    score_args = (
        context,
        evidence["product_history"],
        evidence["keyword_rank_history"],
    )
    baseline = score_shadow_context(
        *score_args,
        strategy_profile=baseline_profile,
        as_of=calculated_at,
    )
    custom = score_shadow_context(
        *score_args,
        strategy_profile=custom_profile,
        as_of=calculated_at,
    )
    recommendation_code, recommendation_reason = _profile_recommendation(custom, config)
    custom["recommendation_code"] = recommendation_code
    custom["recommendation_label"] = RECOMMENDATION_LABELS[recommendation_code]
    custom["recommendation_reason"] = recommendation_reason
    baseline_specialized = _specialized_score(baseline, config["axis_blend"])
    custom_specialized = _specialized_score(custom, config["axis_blend"])
    applicability = applicability or _profile_applicability(
        db,
        version,
        product_id=int(context["product_id"]),
        keyword_id=int(context["keyword_id"]) if context.get("keyword_id") is not None else None,
    )
    applicability["minimum_required"] = config["minimum_applicability"]
    applicability["eligible"] = applicability["score"] >= config["minimum_applicability"]
    if applicability["eligible"]:
        decision = {
            "code": recommendation_code,
            "label": RECOMMENDATION_LABELS[recommendation_code],
            "reason": recommendation_reason,
        }
    else:
        decision = {
            "code": "scope_review",
            "label": "仅试算",
            "reason": f"模型适用度仅 {applicability['score']:.0f}，低于要求的 {config['minimum_applicability']:.0f}；请先核对类目或利基归属。",
        }
    comparison = {
        "baseline_specialized_score": baseline_specialized,
        "custom_specialized_score": custom_specialized,
        "specialized_score_delta": round(custom_specialized - baseline_specialized, 2),
        "opportunity_delta": round(custom["opportunity_score"] - baseline["opportunity_score"], 2),
        "risk_delta": round(custom["risk_score"] - baseline["risk_score"], 2),
        "drivers": _difference_drivers(baseline, custom, config["axis_blend"]),
    }
    source_payload = {
        "profile_version_id": version["id"],
        "config_sha256": version["config_sha256"],
        "product_snapshot_id": context.get("product_snapshot_id"),
        "rank_snapshot_id": context.get("rank_snapshot_id"),
        "keyword_id": context.get("keyword_id"),
    }
    evaluation_fingerprint = hashlib.sha256(
        json.dumps(source_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "profile": profile,
        "version": version,
        "context": {
            "asin": str(context.get("asin") or ""),
            "keyword_id": context.get("keyword_id"),
            "keyword": context.get("keyword"),
            "snapshot_at": str(context.get("snapshot_at") or ""),
            "context_scope": context.get("context_scope") or "product_keyword",
            "selection_warning": evidence.get("selection_warning"),
            "available_contexts": evidence.get("available_contexts") or [],
        },
        "baseline": {**baseline, "specialized_score": baseline_specialized},
        "custom": {**custom, "specialized_score": custom_specialized},
        "comparison": comparison,
        "applicability": applicability,
        "decision": decision,
        "evaluation_fingerprint": evaluation_fingerprint,
        "generated_at": calculated_at.isoformat(sep=" ", timespec="seconds"),
        "writes_production_score": False,
        "boundaries": [
            "专项评分表达当前模型下的研究偏好，不是利润、销量或真实转化率预测。",
            "当前标准化仍沿用评分 V2 通用信号，价格风险尚未按类目与成本校准。",
            "本次结果不写入生产综合分，也不改变任何榜单。",
        ],
    }


def _load_domain_validation_candidates(
    db: MySQLClient,
    profile: Mapping[str, Any],
    version: Mapping[str, Any],
    *,
    search: str,
    sample_limit: int,
) -> dict[str, Any]:
    marketplace = str(profile.get("marketplace") or "US")
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.id AS product_id, p.asin, p.title, p.title_zh,
                       p.product_url, p.image_url, p.category_path,
                       snap.id AS product_snapshot_id, snap.snapshot_at
                FROM products p
                JOIN product_snapshots snap
                  ON snap.id = (
                    SELECT snap2.id
                    FROM product_snapshots snap2
                    WHERE snap2.product_id = p.id
                    ORDER BY snap2.snapshot_at DESC, snap2.id DESC
                    LIMIT 1
                  )
                WHERE p.marketplace = %s
                """,
                (marketplace,),
            )
            products = [dict(row) for row in cursor.fetchall()]
            product_ids = [int(row["product_id"]) for row in products]
            bsr_categories = _latest_bsr_categories(cursor, product_ids)
            direct_members: set[int] = set()
            niche_keywords: dict[int, list[dict[str, Any]]] = {}
            niche_id = version.get("niche_id")
            if niche_id:
                cursor.execute(
                    "SELECT product_id FROM niche_products WHERE niche_id = %s",
                    (niche_id,),
                )
                direct_members = {int(row["product_id"]) for row in cursor.fetchall()}
                cursor.execute(
                    """
                    SELECT krs.product_id, k.id AS keyword_id, k.keyword, krs.snapshot_at
                    FROM niche_keywords nk
                    JOIN keywords k ON k.id = nk.keyword_id
                    JOIN keyword_rank_snapshots krs ON krs.keyword_id = nk.keyword_id
                    JOIN (
                      SELECT keyword_id, MAX(snapshot_at) AS snapshot_at
                      FROM keyword_rank_snapshots
                      GROUP BY keyword_id
                    ) latest
                      ON latest.keyword_id = krs.keyword_id
                     AND latest.snapshot_at = krs.snapshot_at
                    WHERE nk.niche_id = %s AND k.marketplace = %s
                    """,
                    (niche_id, marketplace),
                )
                for raw in cursor.fetchall():
                    row = dict(raw)
                    niche_keywords.setdefault(int(row["product_id"]), []).append(row)

    scope_type = str(version.get("scope_type") or "marketplace")
    normalized_scope = _normalize_category(version.get("category_scope"))
    candidates: list[dict[str, Any]] = []
    for product in products:
        product_id = int(product["product_id"])
        observed_categories = [str(product.get("category_path") or "").strip()]
        observed_categories.extend(bsr_categories.get(product_id) or [])
        observed_categories = list(dict.fromkeys(value for value in observed_categories if value))
        matched_category = next(
            (
                value
                for value in observed_categories
                if _category_matches(normalized_scope, _normalize_category(value))
            ),
            None,
        )
        direct_member = product_id in direct_members
        keyword_rows = niche_keywords.get(product_id) or []
        keyword_rows.sort(key=lambda row: str(row.get("snapshot_at") or ""), reverse=True)
        keyword_member = bool(keyword_rows)
        eligible = (
            scope_type == "marketplace"
            or scope_type == "category" and bool(matched_category)
            or scope_type == "niche" and (direct_member or keyword_member)
            or scope_type == "hybrid" and (bool(matched_category) or direct_member or keyword_member)
        )
        if not eligible:
            continue
        evidence_labels: list[str] = []
        if scope_type == "marketplace":
            evidence_labels.append("站点内有效商品快照")
        if matched_category:
            evidence_labels.append(f"类目：{matched_category}")
        if direct_member:
            evidence_labels.append("人工加入利基")
        if keyword_member:
            evidence_labels.append(f"利基关键词：{keyword_rows[0]['keyword']}")
        candidates.append(
            {
                **product,
                "asin": str(product.get("asin") or "").upper(),
                "observed_categories": observed_categories,
                "matched_category": matched_category,
                "niche_product_member": direct_member,
                "niche_keyword_ids": [int(row["keyword_id"]) for row in keyword_rows],
                "preferred_keyword": str(keyword_rows[0]["keyword"]) if keyword_rows else None,
                "scope_evidence": evidence_labels,
                "scope_evidence_strength": (
                    (100 if direct_member else 0)
                    + (80 if matched_category else 0)
                    + (60 if keyword_member else 0)
                    + (20 if scope_type == "marketplace" else 0)
                ),
            }
        )
    scope_candidate_total = len(candidates)
    if search:
        search_folded = search.casefold()
        candidates = [
            row
            for row in candidates
            if search_folded in str(row.get("asin") or "").casefold()
            or search_folded in str(row.get("title") or "").casefold()
            or search_folded in str(row.get("title_zh") or "").casefold()
        ]
    candidates.sort(key=lambda row: str(row.get("asin") or ""))
    candidates.sort(key=lambda row: str(row.get("snapshot_at") or ""), reverse=True)
    candidates.sort(key=lambda row: int(row.get("scope_evidence_strength") or 0), reverse=True)
    return {
        "scope_candidate_total": scope_candidate_total,
        "search_candidate_total": len(candidates),
        "sample": candidates[:sample_limit],
    }


def _latest_bsr_categories(cursor: Any, product_ids: Sequence[int]) -> dict[int, list[str]]:
    categories: dict[int, list[str]] = {}
    for start in range(0, len(product_ids), 800):
        chunk = list(product_ids[start:start + 800])
        if not chunk:
            continue
        placeholders = ", ".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT b.product_id, b.category_name
            FROM product_bsr_snapshots b
            JOIN (
              SELECT product_id, MAX(snapshot_at) AS snapshot_at
              FROM product_bsr_snapshots
              WHERE product_id IN ({placeholders})
              GROUP BY product_id
            ) latest
              ON latest.product_id = b.product_id
             AND latest.snapshot_at = b.snapshot_at
            ORDER BY b.product_id, b.is_primary DESC, b.rank_value
            """,
            chunk,
        )
        for row in cursor.fetchall():
            value = str(row.get("category_name") or "").strip()
            if value:
                categories.setdefault(int(row["product_id"]), []).append(value)
    return categories


def _domain_batch_row(
    candidate: Mapping[str, Any],
    evidence: Mapping[str, Any],
    evaluated: Mapping[str, Any],
) -> dict[str, Any]:
    custom = evaluated["custom"]
    comparison = evaluated["comparison"]
    context = evaluated["context"]
    delta = float(comparison["specialized_score_delta"])
    warnings = [str(evidence.get("selection_warning") or "").strip()]
    warnings.extend(
        str(item.get("reason") or "").strip()
        for item in custom.get("confidence_components") or []
        if item.get("status") != "observed"
    )
    warnings = list(dict.fromkeys(value for value in warnings if value))
    return {
        "asin": candidate["asin"],
        "title": candidate.get("title"),
        "title_zh": candidate.get("title_zh"),
        "product_url": candidate.get("product_url"),
        "image_url": candidate.get("image_url"),
        "keyword_id": context.get("keyword_id"),
        "keyword": context.get("keyword"),
        "context_scope": context.get("context_scope") or "product_keyword",
        "snapshot_at": context.get("snapshot_at"),
        "scope_evidence": candidate.get("scope_evidence") or [],
        "baseline_specialized_score": comparison["baseline_specialized_score"],
        "custom_specialized_score": comparison["custom_specialized_score"],
        "specialized_score_delta": round(delta, 2),
        "abs_delta": round(abs(delta), 2),
        "opportunity_score": custom["opportunity_score"],
        "risk_score": custom["risk_score"],
        "confidence_score": custom["confidence_score"],
        "confidence_level": custom.get("confidence_level"),
        "applicability": evaluated["applicability"],
        "decision": evaluated["decision"],
        "drivers": comparison.get("drivers") or [],
        "evidence_warnings": warnings[:5],
        "flags": {
            "changed": abs(delta) >= 0.01,
            "low_confidence": float(custom["confidence_score"]) < 55.0,
            "scope_review": not bool(evaluated["applicability"]["eligible"]),
        },
        "evaluation_fingerprint": evaluated["evaluation_fingerprint"],
    }


def _profile_parameter_comparison(config: Mapping[str, Any]) -> dict[str, Any]:
    defaults = build_default_profile_config(str(config.get("base_strategy") or "balanced"))
    changes: list[dict[str, Any]] = []
    sections = (
        ("opportunity_weights", OPPORTUNITY_LABELS),
        ("risk_weights", RISK_LABELS),
        ("axis_blend", {"opportunity": "机会轴占比", "risk_control": "风险控制占比"}),
        ("recommendation_policy", RECOMMENDATION_POLICY_LABELS),
    )
    for section, labels in sections:
        for key, label in labels.items():
            baseline = float(defaults[section][key])
            custom = float(config[section][key])
            if abs(custom - baseline) >= 0.0001:
                changes.append(
                    {
                        "section": section,
                        "key": key,
                        "label": label,
                        "baseline": baseline,
                        "custom": custom,
                    }
                )
    for key, label in (
        ("minimum_applicability", "最低模型适用度"),
        ("trend_priority_guard", "趋势证据保护"),
    ):
        if config.get(key) != defaults.get(key):
            changes.append(
                {
                    "section": "policy",
                    "key": key,
                    "label": label,
                    "baseline": defaults.get(key),
                    "custom": config.get(key),
                }
            )
    identical = not changes
    return {
        "identical_to_parent": identical,
        "changed_parameter_count": len(changes),
        "changes": changes,
        "message": (
            "当前版本参数与父策略完全一致，专项分不会产生参数差异；本次主要验证范围与证据适用性。"
            if identical
            else f"当前版本相对父策略调整了 {len(changes)} 个参数；请重点复核分差较大且证据充分的商品。"
        ),
    }


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _profile_recommendation(row: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[str, str]:
    policy = config["recommendation_policy"]
    opportunity = float(row.get("opportunity_score") or 0.0)
    risk = float(row.get("risk_score") or 0.0)
    confidence = float(row.get("confidence_score") or 0.0)
    trend = row.get("trend") or {}
    if config.get("trend_priority_guard") and (
        int(trend.get("signal_point_count") or 0) < 2
        or float(trend.get("confidence_score") or 0.0) < 0.6
    ):
        return "pause", "该领域模型启用了趋势保护；当前多时间点趋势证据不足，先补采同关键词快照"
    if confidence < policy["minimum_confidence"]:
        return "pause", "关键字段、趋势或排名证据不足，当前专项分只适合补证据"
    if opportunity >= policy["high_opportunity"] and risk >= policy["high_risk"]:
        return "benchmark_only", "专项机会信号与领域风险同时偏高，适合作为对标和差异化研究对象"
    if risk >= policy["critical_risk"]:
        return "pause", "专项风险超过模型的极高风险门槛，暂不进入验证池"
    if (
        opportunity >= policy["priority_opportunity"]
        and risk <= policy["priority_max_risk"]
        and confidence >= policy["priority_confidence"]
    ):
        return "priority_validate", "专项机会、风险和证据同时达到该领域模型阈值，建议进入人工验证"
    if (
        opportunity >= policy["observe_opportunity"]
        and risk <= policy["observe_max_risk"]
        and confidence >= policy["observe_confidence"]
    ):
        return "observe", "当前符合该领域模型的观察门槛，建议继续积累证据"
    if opportunity >= policy["fallback_benchmark_opportunity"]:
        return "benchmark_only", "存在领域研究信号，但风险或证据尚不支持进入验证池"
    return "pause", "当前专项机会信号未达到该领域模型的最低研究门槛"


def _specialized_score(row: Mapping[str, Any], blend: Mapping[str, Any]) -> float:
    value = (
        float(row.get("opportunity_score") or 0.0) * float(blend["opportunity"])
        + (100.0 - float(row.get("risk_score") or 0.0)) * float(blend["risk_control"])
    )
    return round(max(0.0, min(100.0, value)), 2)


def _difference_drivers(
    baseline: Mapping[str, Any],
    custom: Mapping[str, Any],
    blend: Mapping[str, Any],
) -> list[dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    for axis, key, direction in (
        ("opportunity", "opportunity_components", 1.0),
        ("risk", "risk_components", -1.0),
    ):
        baseline_items = {item["key"]: item for item in baseline.get(key) or []}
        for item in custom.get(key) or []:
            previous = baseline_items.get(item["key"]) or {}
            axis_weight = float(blend["opportunity"] if axis == "opportunity" else blend["risk_control"])
            effect = direction * axis_weight * (
                float(item.get("contribution") or 0.0) - float(previous.get("contribution") or 0.0)
            )
            if abs(effect) < 0.01:
                continue
            drivers.append(
                {
                    "axis": axis,
                    "key": item["key"],
                    "label": item["label"],
                    "score_effect": round(effect, 2),
                    "baseline_weight": previous.get("weight"),
                    "custom_weight": item.get("weight"),
                    "message": f"{item['label']}权重使专项分{'提高' if effect > 0 else '降低'} {abs(effect):.2f}",
                }
            )
    drivers.sort(key=lambda item: abs(float(item["score_effect"])), reverse=True)
    return drivers[:5]


def _profile_applicability(
    db: MySQLClient,
    version: Mapping[str, Any],
    *,
    product_id: int,
    keyword_id: int | None,
) -> dict[str, Any]:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT category_path FROM products WHERE id = %s LIMIT 1", (product_id,))
            product = cursor.fetchone() or {}
            cursor.execute(
                """
                SELECT category_name
                FROM product_bsr_snapshots
                WHERE product_id = %s
                  AND snapshot_at = (
                    SELECT MAX(snapshot_at) FROM product_bsr_snapshots WHERE product_id = %s
                  )
                ORDER BY is_primary DESC, rank_value ASC
                """,
                (product_id, product_id),
            )
            bsr_categories = [str(row.get("category_name") or "") for row in cursor.fetchall()]
            product_member = False
            keyword_member = False
            niche_id = version.get("niche_id")
            if niche_id:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM niche_products WHERE niche_id = %s AND product_id = %s) AS present",
                    (niche_id, product_id),
                )
                product_member = bool((cursor.fetchone() or {}).get("present"))
                if keyword_id is not None:
                    cursor.execute(
                        "SELECT EXISTS(SELECT 1 FROM niche_keywords WHERE niche_id = %s AND keyword_id = %s) AS present",
                        (niche_id, keyword_id),
                    )
                    keyword_member = bool((cursor.fetchone() or {}).get("present"))
    categories = [str(product.get("category_path") or "").strip(), *bsr_categories]
    categories = [value for value in categories if value]
    return _profile_applicability_from_facts(
        version,
        categories=categories,
        product_member=product_member,
        keyword_member=keyword_member,
        keyword_id=keyword_id,
    )


def _profile_applicability_from_facts(
    version: Mapping[str, Any],
    *,
    categories: Sequence[str],
    product_member: bool,
    keyword_member: bool,
    keyword_id: int | None,
) -> dict[str, Any]:
    category_values = list(dict.fromkeys(str(value).strip() for value in categories if str(value).strip()))
    category_score, category_state = _category_applicability(version.get("category_scope"), category_values)
    if product_member:
        niche_score, niche_state = 100.0, "商品已由用户明确加入该利基"
    elif keyword_member:
        niche_score, niche_state = 80.0, "当前评分关键词属于该利基，但商品尚未人工确认"
    elif version.get("niche_id") and keyword_id is None:
        niche_score, niche_state = 20.0, "商品未加入该利基，且当前商品级试算没有关键词归属证据"
    elif version.get("niche_id"):
        niche_score, niche_state = 20.0, "商品和当前关键词均未加入该利基"
    else:
        niche_score, niche_state = 0.0, "该版本未绑定市场利基"
    scope_type = str(version.get("scope_type") or "marketplace")
    if scope_type == "marketplace":
        score = 100.0
        message = "模型适用于当前站点的全部商品；这不是类目专项匹配。"
    elif scope_type == "category":
        score = category_score
        message = category_state
    elif scope_type == "niche":
        score = niche_score
        message = niche_state
    else:
        score = round(category_score * 0.60 + niche_score * 0.40, 2)
        message = f"类目：{category_state}；利基：{niche_state}"
    level = "高" if score >= 80 else "中" if score >= 60 else "低" if score >= 35 else "不匹配"
    return {
        "score": round(score, 2),
        "level": level,
        "scope_type": scope_type,
        "scope_label": PROFILE_SCOPE_LABELS.get(scope_type, scope_type),
        "message": message,
        "category_scope": version.get("category_scope"),
        "observed_categories": category_values,
        "niche_id": version.get("niche_id"),
        "niche_name": version.get("niche_name"),
        "niche_product_member": product_member,
        "niche_keyword_member": keyword_member,
    }


def _category_applicability(scope: Any, categories: list[str]) -> tuple[float, str]:
    scope_value = _normalize_category(scope)
    if not scope_value:
        return 0.0, "该版本没有填写类目范围"
    if not categories:
        return 35.0, "商品尚未采集可核验的类目或 BSR 类目，只能低适用度试算"
    matched = [value for value in categories if _category_matches(scope_value, _normalize_category(value))]
    if matched:
        return 100.0, f"已匹配类目证据：{matched[0]}"
    return 10.0, "已采集类目与模型范围不匹配"


def _category_matches(scope: str, observed: str) -> bool:
    return bool(scope and observed and (scope in observed or observed in scope))


def _normalize_category(value: Any) -> str:
    text = str(value or "").casefold().replace("&", "and")
    return " ".join("".join(char if char.isalnum() else " " for char in text).split())


def _profile_select() -> str:
    return """
        SELECT
          sp.id, sp.marketplace, sp.name, sp.normalized_name, sp.description,
          sp.status, sp.current_version_id, sp.created_at, sp.updated_at,
          spv.id AS version_id, spv.version_no, spv.parent_model_version,
          spv.config_schema_version, spv.scope_type, spv.category_scope,
          spv.niche_id, mn.name AS niche_name, spv.source_type,
          spv.config_json, spv.config_sha256, spv.change_note,
          spv.created_at AS version_created_at,
          (SELECT COUNT(*) FROM scoring_profile_versions counted WHERE counted.profile_id = sp.id) AS version_count
        FROM scoring_profiles sp
        LEFT JOIN scoring_profile_versions spv ON spv.id = sp.current_version_id
        LEFT JOIN market_niches mn ON mn.id = spv.niche_id
    """


def _profile_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    version = None
    if row.get("version_id") is not None:
        version = _version_from_row(
            {
                "id": row.get("version_id"),
                "profile_id": row.get("id"),
                "version_no": row.get("version_no"),
                "parent_model_version": row.get("parent_model_version"),
                "config_schema_version": row.get("config_schema_version"),
                "scope_type": row.get("scope_type"),
                "category_scope": row.get("category_scope"),
                "niche_id": row.get("niche_id"),
                "niche_name": row.get("niche_name"),
                "source_type": row.get("source_type"),
                "config_json": row.get("config_json"),
                "config_sha256": row.get("config_sha256"),
                "change_note": row.get("change_note"),
                "created_at": row.get("version_created_at"),
            }
        )
    return {
        "id": int(row["id"]),
        "marketplace": str(row.get("marketplace") or "US"),
        "name": str(row.get("name") or ""),
        "description": row.get("description"),
        "status": str(row.get("status") or "draft"),
        "status_label": PROFILE_STATUS_LABELS.get(str(row.get("status") or "draft"), "未知"),
        "current_version_id": int(row["current_version_id"]) if row.get("current_version_id") else None,
        "current_version": version,
        "version_count": int(row.get("version_count") or 0),
        "created_at": _display(row.get("created_at")),
        "updated_at": _display(row.get("updated_at")),
    }


def _version_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_config = row.get("config_json")
    if isinstance(raw_config, str):
        raw_config = json.loads(raw_config)
    return {
        "id": int(row["id"]),
        "profile_id": int(row["profile_id"]),
        "version_no": int(row["version_no"]),
        "parent_model_version": str(row.get("parent_model_version") or MODEL_VERSION),
        "config_schema_version": str(row.get("config_schema_version") or ""),
        "scope_type": str(row.get("scope_type") or "marketplace"),
        "scope_label": PROFILE_SCOPE_LABELS.get(str(row.get("scope_type") or "marketplace"), "未知"),
        "category_scope": row.get("category_scope"),
        "niche_id": int(row["niche_id"]) if row.get("niche_id") else None,
        "niche_name": row.get("niche_name"),
        "source_type": str(row.get("source_type") or "manual"),
        "config": _normalize_value(raw_config or {}),
        "config_sha256": str(row.get("config_sha256") or ""),
        "change_note": row.get("change_note"),
        "created_at": _display(row.get("created_at")),
    }


def _insert_version(
    cursor: Any,
    *,
    profile_id: int,
    version_no: int,
    scope: Mapping[str, Any],
    config: Mapping[str, Any],
    config_hash: str,
    change_note: str | None,
) -> int:
    cursor.execute(
        """
        INSERT INTO scoring_profile_versions (
          profile_id, version_no, parent_model_version, config_schema_version,
          scope_type, category_scope, niche_id, source_type,
          config_json, config_sha256, change_note
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'manual', %s, %s, %s)
        """,
        (
            profile_id,
            version_no,
            MODEL_VERSION,
            PROFILE_CONFIG_SCHEMA_VERSION,
            scope["scope_type"],
            scope.get("category_scope"),
            scope.get("niche_id"),
            json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            config_hash,
            change_note,
        ),
    )
    return int(cursor.lastrowid)


def _require_profile(cursor: Any, profile_id: int, *, for_update: bool = False) -> dict[str, Any]:
    suffix = " FOR UPDATE" if for_update else ""
    cursor.execute(f"SELECT * FROM scoring_profiles WHERE id = %s{suffix}", (profile_id,))
    row = cursor.fetchone()
    if not row:
        raise DomainScoringError(f"未找到领域模型 #{profile_id}")
    return row


def _validate_scope_reference(cursor: Any, marketplace: str, scope: Mapping[str, Any]) -> None:
    niche_id = scope.get("niche_id")
    if not niche_id:
        return
    cursor.execute("SELECT marketplace FROM market_niches WHERE id = %s LIMIT 1", (niche_id,))
    row = cursor.fetchone()
    if not row:
        raise DomainScoringError(f"未找到市场利基 #{niche_id}")
    if str(row.get("marketplace") or "").upper() != marketplace:
        raise DomainScoringError("领域模型与市场利基必须属于同一站点")


def _scope_values(scope_type: Any, category_scope: Any, niche_id: Any) -> dict[str, Any]:
    scope_type_value = _choice(scope_type, PROFILE_SCOPE_TYPES, "适用范围")
    category_value = _optional_text(category_scope, 512)
    niche_value = None if niche_id in (None, "", 0, "0") else _positive_int(niche_id, "市场利基 ID")
    if scope_type_value == "category" and not category_value:
        raise DomainScoringError("类目模型必须填写 Amazon 类目范围")
    if scope_type_value == "niche" and not niche_value:
        raise DomainScoringError("利基模型必须选择市场利基")
    if scope_type_value == "hybrid" and (not category_value or not niche_value):
        raise DomainScoringError("类目+利基模型必须同时填写类目范围并选择市场利基")
    if scope_type_value == "marketplace":
        category_value = None
        niche_value = None
    elif scope_type_value == "category":
        niche_value = None
    elif scope_type_value == "niche":
        category_value = None
    return {
        "scope_type": scope_type_value,
        "category_scope": category_value,
        "niche_id": niche_value,
    }


def _profile_config_hash(scope: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    payload = {
        "schema_version": PROFILE_CONFIG_SCHEMA_VERSION,
        "parent_model_version": MODEL_VERSION,
        "scope": dict(scope),
        "config": dict(config),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _weight_map(values: Mapping[str, Any], keys: tuple[str, ...], label: str) -> dict[str, float]:
    unknown = sorted(set(values) - set(keys))
    missing = [key for key in keys if key not in values]
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"未知项 {'、'.join(unknown)}")
        if missing:
            details.append(f"缺少 {'、'.join(missing)}")
        raise DomainScoringError(f"{label}不完整：{'；'.join(details)}")
    normalized = {key: round(_ratio(values[key], f"{label} {key}"), 6) for key in keys}
    total = sum(normalized.values())
    if abs(total - 1.0) > 0.001:
        raise DomainScoringError(f"{label}合计必须为 1，当前为 {total:.4f}")
    return normalized


def _validate_recommendation_policy(policy: Mapping[str, float]) -> None:
    if policy["priority_opportunity"] < policy["observe_opportunity"]:
        raise DomainScoringError("优先验证机会门槛不能低于观察池机会门槛")
    if policy["priority_max_risk"] > policy["observe_max_risk"]:
        raise DomainScoringError("优先验证最高风险不能高于观察池最高风险")
    if policy["priority_confidence"] < policy["observe_confidence"]:
        raise DomainScoringError("优先验证置信门槛不能低于观察池置信门槛")
    if policy["critical_risk"] < policy["high_risk"]:
        raise DomainScoringError("极高风险门槛不能低于高机会高风险门槛")


def _base_strategy(code: Any) -> StrategyProfile:
    normalized = str(code or "balanced").strip()
    profile = STRATEGIES.get(normalized)
    if not profile:
        raise DomainScoringError(f"未知通用基线策略：{normalized}")
    return profile


def _marketplace(value: Any) -> str:
    normalized = str(value or "US").strip().upper() or "US"
    if not re.fullmatch(r"[A-Z0-9_-]{2,16}", normalized):
        raise DomainScoringError("站点格式不正确")
    return normalized


def _choice(value: Any, choices: set[str], label: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in choices:
        raise DomainScoringError(f"{label}不正确：{normalized or '空'}")
    return normalized


def _ratio(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DomainScoringError(f"{label}必须是 0-1 之间的数字") from exc
    if not 0.0 <= number <= 1.0:
        raise DomainScoringError(f"{label}必须在 0-1 之间")
    return number


def _score(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DomainScoringError(f"{label}必须是 0-100 之间的数字") from exc
    if not 0.0 <= number <= 100.0:
        raise DomainScoringError(f"{label}必须在 0-100 之间")
    return number


def _positive_int(value: Any, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise DomainScoringError(f"{label}必须是正整数") from exc
    if number <= 0:
        raise DomainScoringError(f"{label}必须是正整数")
    return number


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _required_text(value: Any, label: str, maximum: int) -> str:
    normalized = " ".join(str(value or "").strip().split())
    if not normalized:
        raise DomainScoringError(f"{label}不能为空")
    if len(normalized) > maximum:
        raise DomainScoringError(f"{label}不能超过 {maximum} 个字符")
    return normalized


def _optional_text(value: Any, maximum: int) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise DomainScoringError(f"文本不能超过 {maximum} 个字符")
    return normalized


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _display(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value
