"""Evidence-aware product metrics, exact inputs, and versioned scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
import json
import math
import statistics
from typing import Any, Iterable

from database.mysql_client import MySQLClient
from services.amazon_urls import normalize_asin
from services.product_pool import build_product_capture_history


MODEL_VERSION = "metric-center-v1.0"
DEFAULT_CVR_SCENARIO = (0.03, 0.08, 0.15)
SOURCE_TYPES = {"manual", "csv", "sp_api", "ads_api", "third_party"}

COUNT_FIELDS = {
    "sessions",
    "page_views",
    "units_ordered",
    "orders",
    "impressions",
    "clicks",
    "cart_adds",
    "purchases",
    "ad_clicks",
    "ad_orders",
}
MONEY_FIELDS = {
    "ordered_sales",
    "ad_spend",
    "ad_sales",
    "total_sales",
    "unit_purchase_cost",
    "unit_shipping_cost",
    "unit_fba_fee",
    "unit_referral_fee",
    "unit_other_cost",
}
RATIO_FIELDS = {
    "featured_offer_percentage",
    "assumed_cvr_low",
    "assumed_cvr_base",
    "assumed_cvr_high",
}
INPUT_COLUMNS = (
    "source_label",
    *sorted(COUNT_FIELDS),
    *sorted(MONEY_FIELDS),
    *sorted(RATIO_FIELDS),
    "notes",
)


class MetricInputError(ValueError):
    """Raised when seller-provided metric data is invalid."""


@dataclass(frozen=True)
class EstimateValue:
    key: str
    label: str
    low: float | None
    base: float | None
    high: float | None
    unit: str
    confidence_score: float
    confidence_level: str
    method: str
    evidence: dict[str, Any]
    model_version: str = MODEL_VERSION
    layer: str = "经验估算"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_metric_products_page(
    *,
    limit: int = 50,
    offset: int = 0,
    keyword: str | None = None,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    limit_value = max(1, min(int(limit or 50), 200))
    offset_value = max(0, int(offset or 0))
    search = str(keyword or "").strip()
    where = ""
    params: list[Any] = []
    if search:
        where = "WHERE p.asin LIKE %s OR p.title LIKE %s OR COALESCE(p.title_zh, '') LIKE %s"
        pattern = f"%{search}%"
        params.extend((pattern, pattern, pattern))

    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS total FROM products p {where}", params)
            total = int((cursor.fetchone() or {}).get("total") or 0)
            cursor.execute(
                f"""
                SELECT
                  p.asin, p.marketplace, p.title, p.title_zh, p.category_path,
                  p.date_first_available, p.detail_collected_at,
                  s.snapshot_at, s.price, s.rating, s.review_count, s.monthly_bought,
                  o.current_price AS detail_price, o.availability_status,
                  o.fulfillment_channel, o.is_prime,
                  EXISTS(SELECT 1 FROM product_metric_inputs mi WHERE mi.product_id = p.id) AS has_metric_input,
                  (SELECT MAX(mi2.period_end) FROM product_metric_inputs mi2 WHERE mi2.product_id = p.id) AS latest_input_period
                FROM products p
                LEFT JOIN product_snapshots s
                  ON s.product_id = p.id
                 AND s.snapshot_at = (SELECT MAX(s2.snapshot_at) FROM product_snapshots s2 WHERE s2.product_id = p.id)
                LEFT JOIN product_offer_snapshots o
                  ON o.product_id = p.id
                 AND o.snapshot_at = (SELECT MAX(o2.snapshot_at) FROM product_offer_snapshots o2 WHERE o2.product_id = p.id)
                {where}
                ORDER BY COALESCE(p.detail_collected_at, s.snapshot_at, p.last_seen_at) DESC, p.asin ASC
                LIMIT %s OFFSET %s
                """,
                [*params, limit_value, offset_value],
            )
            rows = cursor.fetchall()
    normalized = [_normalize_value(row) for row in rows]
    for row in normalized:
        monthly = _number(row.get("monthly_bought"))
        price = _number(row.get("detail_price")) or _number(row.get("price"))
        row["monthly_gmv_floor_proxy"] = round(monthly * price, 2) if monthly is not None and price else None
        row["has_metric_input"] = bool(row.get("has_metric_input"))
    return {"rows": normalized, "total": total, "limit": limit_value, "offset": offset_value}


def get_product_metric_center(
    asin: str,
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    normalized_asin = normalize_asin(asin)
    db = client or MySQLClient()
    context = _fetch_context(db, normalized_asin)
    if not context["product"]:
        raise MetricInputError(f"商品库中不存在 ASIN：{normalized_asin}")

    product = context["product"]
    snapshots = context["snapshots"]
    offers = context["offers"]
    capture_history = _metric_capture_history(snapshots, offers)
    bsr_rows = context["bsr"]
    latest_input = context["inputs"][0] if context["inputs"] else None
    current_price = _current_price(capture_history, offers)
    deterministic = calculate_deterministic_metrics(
        product=product,
        snapshots=capture_history,
        offers=offers,
        bsr_rows=bsr_rows,
        specs=context["physical_specs"],
        variants=context["variants"],
        category_benchmark=context["category_benchmark"],
    )
    exact = calculate_exact_metrics(latest_input, current_price=current_price) if latest_input else []
    estimates = build_product_estimates(
        product=product,
        snapshots=capture_history,
        offers=offers,
        specs=context["physical_specs"],
        latest_input=latest_input,
        deterministic_metrics=deterministic,
        category_benchmark=context["category_benchmark"],
    )
    quality = build_data_quality(
        product=product,
        snapshots=snapshots,
        offers=offers,
        specs=context["physical_specs"],
        serp_contexts=context["serp_contexts"],
        inputs=context["inputs"],
    )
    return {
        "product": product,
        "latest_snapshot": snapshots[-1] if snapshots else None,
        "latest_observation": capture_history[-1] if capture_history else None,
        "capture_history": capture_history,
        "latest_offer": offers[-1] if offers else None,
        "physical_specs": context["physical_specs"],
        "variants": context["variants"],
        "latest_bsr": _latest_bsr_group(bsr_rows),
        "serp_contexts": context["serp_contexts"],
        "deterministic_metrics": deterministic,
        "exact_metrics": exact,
        "estimates": [item.to_dict() for item in estimates],
        "inputs": context["inputs"],
        "saved_estimates": context["saved_estimates"],
        "data_quality": quality,
        "model": {
            "version": MODEL_VERSION,
            "default_cvr_scenario": {
                "low": DEFAULT_CVR_SCENARIO[0],
                "base": DEFAULT_CVR_SCENARIO[1],
                "high": DEFAULT_CVR_SCENARIO[2],
            },
            "bsr_sales_model": "disabled_until_calibrated",
            "score_integration": "disabled",
        },
    }


def save_metric_input(
    asin: str,
    payload: dict[str, Any],
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    normalized_asin = normalize_asin(asin)
    normalized = normalize_metric_input(payload)
    db = client or MySQLClient()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            product_id = _product_id(cursor, normalized_asin)
            if product_id is None:
                raise MetricInputError(f"商品库中不存在 ASIN：{normalized_asin}")
            _upsert_metric_input(cursor, product_id, normalized, payload)
    return {
        "asin": normalized_asin,
        "input": normalized,
        "estimates": refresh_product_estimates(normalized_asin, client=db)["estimates"],
    }


def import_metric_inputs(
    rows: Iterable[dict[str, Any]],
    *,
    client: MySQLClient | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    saved = 0
    rejected: list[dict[str, Any]] = []
    touched: set[str] = set()
    with db.connect() as conn:
        with conn.cursor() as cursor:
            for index, raw in enumerate(rows, start=1):
                try:
                    asin = normalize_asin(str(raw.get("asin") or raw.get("ASIN") or ""))
                    normalized = normalize_metric_input(raw, default_source="csv")
                    product_id = _product_id(cursor, asin)
                    if product_id is None:
                        raise MetricInputError(f"商品库中不存在 ASIN：{asin}")
                    _upsert_metric_input(cursor, product_id, normalized, raw)
                    saved += 1
                    touched.add(asin)
                except Exception as exc:
                    rejected.append({"row": index, "message": str(exc)})
    refreshed = []
    for asin in sorted(touched):
        refreshed.append(refresh_product_estimates(asin, client=db)["asin"])
    return {"total": saved + len(rejected), "saved": saved, "rejected": rejected, "refreshed_asins": refreshed}


def refresh_product_estimates(
    asin: str,
    *,
    client: MySQLClient | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    db = client or MySQLClient()
    center = get_product_metric_center(asin, client=db)
    normalized_asin = center["product"]["asin"]
    estimate_date = as_of_date or date.today()
    estimates = [EstimateValue(**_estimate_constructor_payload(item)) for item in center["estimates"]]
    with db.connect() as conn:
        with conn.cursor() as cursor:
            product_id = _product_id(cursor, normalized_asin)
            if product_id is None:
                raise MetricInputError(f"商品库中不存在 ASIN：{normalized_asin}")
            for estimate in estimates:
                _upsert_estimate(cursor, product_id, estimate_date, estimate)
    return {
        "asin": normalized_asin,
        "as_of_date": estimate_date.isoformat(),
        "model_version": MODEL_VERSION,
        "estimates": [item.to_dict() for item in estimates],
    }


def normalize_metric_input(
    payload: dict[str, Any],
    *,
    default_source: str = "manual",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MetricInputError("指标输入必须是对象。")
    start = _parse_date(payload.get("period_start"), "统计周期开始")
    end = _parse_date(payload.get("period_end"), "统计周期结束")
    if end < start:
        raise MetricInputError("统计周期结束不能早于开始。")
    source_type = str(payload.get("source_type") or default_source).strip().casefold()
    if source_type not in SOURCE_TYPES:
        raise MetricInputError("来源类型必须是 manual/csv/sp_api/ads_api/third_party 之一。")
    result: dict[str, Any] = {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "source_type": source_type,
        "source_label": _short_text(payload.get("source_label"), 255),
        "notes": _short_text(payload.get("notes"), 4000),
    }
    for field in COUNT_FIELDS:
        result[field] = _nonnegative_int(payload.get(field), field)
    for field in MONEY_FIELDS:
        result[field] = _nonnegative_number(payload.get(field), field)
    for field in RATIO_FIELDS:
        result[field] = _ratio(payload.get(field), field)
    scenario = tuple(result[field] for field in ("assumed_cvr_low", "assumed_cvr_base", "assumed_cvr_high"))
    provided = [value for value in scenario if value is not None]
    if provided and len(provided) != 3:
        raise MetricInputError("经验转化率情景需同时填写低、中、高三个值。")
    if len(provided) == 3 and not (scenario[0] <= scenario[1] <= scenario[2]):
        raise MetricInputError("经验转化率必须满足低情景 <= 中情景 <= 高情景。")
    has_metric = any(result.get(field) is not None for field in COUNT_FIELDS | MONEY_FIELDS | RATIO_FIELDS)
    if not has_metric:
        raise MetricInputError("至少填写一项流量、销售、广告、成本或情景数据。")
    return result


def calculate_exact_metrics(
    row: dict[str, Any] | None,
    *,
    current_price: float | None = None,
) -> list[dict[str, Any]]:
    if not row:
        return []
    metrics: list[dict[str, Any]] = []
    source_type = str(row.get("source_type") or "manual")
    layer = "官方输入" if source_type in {"sp_api", "ads_api"} else "人工输入"

    def add(key: str, label: str, value: float | None, unit: str, formula: str) -> None:
        if value is None or not math.isfinite(value):
            return
        metrics.append(
            {
                "key": key,
                "label": label,
                "value": round(value, 4),
                "unit": unit,
                "layer": layer,
                "formula": formula,
                "source_type": source_type,
                "period_start": row.get("period_start"),
                "period_end": row.get("period_end"),
            }
        )

    sessions = _number(row.get("sessions"))
    units = _number(row.get("units_ordered"))
    orders = _number(row.get("orders"))
    impressions = _number(row.get("impressions"))
    clicks = _number(row.get("clicks"))
    cart_adds = _number(row.get("cart_adds"))
    purchases = _number(row.get("purchases"))
    ad_clicks = _number(row.get("ad_clicks"))
    ad_orders = _number(row.get("ad_orders"))
    ad_spend = _number(row.get("ad_spend"))
    ad_sales = _number(row.get("ad_sales"))
    total_sales = _number(row.get("total_sales"))
    ordered_sales = _number(row.get("ordered_sales"))

    add("unit_session_percentage", "Unit Session Percentage", _safe_percent(units, sessions), "%", "订购件数 / Sessions")
    add("order_conversion_rate", "订单会话转化率", _safe_percent(orders, sessions), "%", "订单数 / Sessions")
    add("ctr", "点击率 CTR", _safe_percent(clicks, impressions), "%", "Clicks / Impressions")
    add("click_to_cart_rate", "点击加购率", _safe_percent(cart_adds, clicks), "%", "Cart Adds / Clicks")
    add("cart_to_purchase_rate", "加购购买率", _safe_percent(purchases, cart_adds), "%", "Purchases / Cart Adds")
    ad_cvr_percent = _safe_percent(ad_orders, ad_clicks)
    add("ad_conversion_rate", "广告转化率", ad_cvr_percent, "%", "Ad Orders / Ad Clicks")
    add("cpc", "平均点击成本 CPC", _safe_divide(ad_spend, ad_clicks), "currency/click", "Ad Spend / Ad Clicks")
    add("acos", "广告投入产出比 ACOS", _safe_percent(ad_spend, ad_sales), "%", "Ad Spend / Ad Sales")
    add("roas", "广告回报 ROAS", _safe_divide(ad_sales, ad_spend), "x", "Ad Sales / Ad Spend")
    add("tacos", "总广告成本比 TACOS", _safe_percent(ad_spend, total_sales), "%", "Ad Spend / Total Sales")
    selling_price = _safe_divide(ordered_sales, units) or current_price
    add("average_selling_price", "平均成交价", _safe_divide(ordered_sales, units), "currency/unit", "Ordered Sales / Units Ordered")

    costs = [
        _number(row.get(field))
        for field in ("unit_purchase_cost", "unit_shipping_cost", "unit_fba_fee", "unit_referral_fee", "unit_other_cost")
    ]
    known_costs = [value for value in costs if value is not None]
    if selling_price is not None and known_costs:
        unit_cost = sum(known_costs)
        contribution = selling_price - unit_cost
        add("unit_contribution_before_ads", "单件广告前贡献", contribution, "currency/unit", "成交价 - 已填写单件成本")
        add("contribution_margin_before_ads", "广告前贡献率", _safe_percent(contribution, selling_price), "%", "单件广告前贡献 / 成交价")
        add("break_even_acos", "盈亏平衡 ACOS", _safe_percent(contribution, selling_price), "%", "单件广告前贡献 / 成交价")
        if ad_cvr_percent is not None:
            add("max_cpc_exact", "精确数据下最高 CPC", contribution * (ad_cvr_percent / 100), "currency/click", "单件广告前贡献 x 广告转化率")
        sales_for_profit = total_sales if total_sales is not None else ordered_sales
        if sales_for_profit is not None and units is not None:
            period_profit = sales_for_profit - unit_cost * units - (ad_spend or 0)
            add("period_contribution_after_ads", "周期广告后贡献", period_profit, "currency", "销售额 - 单件成本 x 件数 - 广告花费")
    return metrics


def calculate_deterministic_metrics(
    *,
    product: dict[str, Any],
    snapshots: list[dict[str, Any]],
    offers: list[dict[str, Any]],
    bsr_rows: list[dict[str, Any]],
    specs: dict[str, Any] | None,
    variants: list[dict[str, Any]],
    category_benchmark: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    latest = snapshots[-1] if snapshots else {}
    current_price = _current_price(snapshots, offers)
    monthly = _number(latest.get("monthly_bought"))

    def add(
        key: str,
        label: str,
        value: float | int | None,
        unit: str,
        explanation: str,
        confidence: str = "中",
    ) -> None:
        if value is None:
            return
        metrics.append(
            {
                "key": key,
                "label": label,
                "value": round(float(value), 4),
                "unit": unit,
                "layer": "确定性计算",
                "explanation": explanation,
                "confidence": confidence,
            }
        )

    add("monthly_units_floor", "近月购买下界", monthly, "units/month", "来自 Amazon 近月购买徽标，是下界而非精确销量。")
    if monthly is not None and current_price is not None:
        add("monthly_gmv_floor_proxy", "当前价 GMV 下界代理", monthly * current_price, "currency/month", "当前价格 x 近月购买下界，不包含折扣、退货和变体差异。")
    first_available = _date_value(product.get("date_first_available"))
    if first_available:
        add("listing_age_days", "Amazon 刊登天数", (date.today() - first_available).days, "days", "按 Date First Available 计算。", "高")
    first_seen = _datetime_value(product.get("first_seen_at"))
    if first_seen:
        add("observed_days", "系统观察天数", (datetime.now() - first_seen).days, "days", "按本系统首次采集时间计算，不等于上架年龄。", "高")

    prices = [_number(row.get("price")) for row in snapshots]
    prices = [value for value in prices if value is not None]
    if len(prices) >= 2 and statistics.mean(prices):
        add("price_volatility", "价格波动系数", statistics.pstdev(prices) / statistics.mean(prices), "ratio", "历史商品采集记录价格标准差 / 均值。")
    deal_flags = [_optional_bool(row.get("is_deal")) for row in snapshots]
    deal_flags = [value for value in deal_flags if value is not None]
    if deal_flags:
        add("promotion_observation_rate", "促销出现率", sum(deal_flags) / len(deal_flags), "ratio", "已判断促销状态的商品采集记录占比。")
    review_velocity = _per_day_delta(snapshots, "review_count")
    add("review_velocity", "评论增长速度", review_velocity, "reviews/day", "首末快照评论数差 / 实际间隔天数。")
    rating_drift = _delta(snapshots, "rating")
    add("rating_drift", "评分漂移", rating_drift, "stars", "最新评分 - 最早评分。")

    known_availability = [row.get("availability_status") for row in offers if row.get("availability_status") not in (None, "unknown")]
    if known_availability:
        add("in_stock_rate", "详情页在售稳定率", sum(value == "in_stock" for value in known_availability) / len(known_availability), "ratio", "有效报价快照中显示 In Stock 的占比。")
    if category_benchmark and category_benchmark.get("percentile") is not None:
        add("bsr_demand_index", "类目 BSR 需求指数", category_benchmark["percentile"], "score/100", f"同类目最新 BSR 样本 {category_benchmark.get('sample_size')} 个的百分位；不是销量估算。")

    potential, confidence = _conversion_potential_score(
        latest_snapshot=latest,
        latest_offer=offers[-1] if offers else None,
        specs=specs,
    )
    add("listing_conversion_potential", "页面转化潜力分", potential, "score/100", "由评分、社会证明、页面内容、履约和报价完整度形成的相对诊断分，不是真实转化率。", _confidence_level(confidence))
    add("variant_count", "已识别变体数", len(variants), "variants", "详情页变体区域中识别的唯一子 ASIN 数。", "中")
    return metrics


def build_product_estimates(
    *,
    product: dict[str, Any],
    snapshots: list[dict[str, Any]],
    offers: list[dict[str, Any]],
    specs: dict[str, Any] | None,
    latest_input: dict[str, Any] | None,
    deterministic_metrics: list[dict[str, Any]],
    category_benchmark: dict[str, Any] | None,
) -> list[EstimateValue]:
    scenario = _cvr_scenario(latest_input)
    source = "商品输入覆盖" if latest_input and latest_input.get("assumed_cvr_low") is not None else "V1 全局经验默认"
    estimates = [
        EstimateValue(
            key="assumed_cvr",
            label="经验转化率情景",
            low=scenario[0],
            base=scenario[1],
            high=scenario[2],
            unit="ratio",
            confidence_score=15.0,
            confidence_level="低",
            method="editable_scenario_assumption",
            evidence={"source": source, "not_competitor_actual_cvr": True},
        )
    ]
    latest = snapshots[-1] if snapshots else {}
    monthly = _number(latest.get("monthly_bought"))
    if monthly is not None and monthly > 0:
        estimates.append(
            EstimateValue(
                key="required_monthly_sessions_for_demand_floor",
                label="满足需求下界所需月会话量",
                low=round(monthly / scenario[2], 2),
                base=round(monthly / scenario[1], 2),
                high=round(monthly / scenario[0], 2),
                unit="sessions/month",
                confidence_score=30.0,
                confidence_level="低",
                method="monthly_bought_floor_divided_by_cvr_scenario",
                evidence={
                    "monthly_bought_floor": monthly,
                    "cvr_scenario": {"low": scenario[0], "base": scenario[1], "high": scenario[2]},
                    "limitation": "近月购买徽标是下界，结果不是竞品真实 Sessions。",
                },
            )
        )
    potential = next((item for item in deterministic_metrics if item["key"] == "listing_conversion_potential"), None)
    if potential:
        _, score_confidence = _conversion_potential_score(
            latest_snapshot=latest,
            latest_offer=offers[-1] if offers else None,
            specs=specs,
        )
        estimates.append(
            EstimateValue(
                key="listing_conversion_potential",
                label="页面转化潜力分",
                low=potential["value"],
                base=potential["value"],
                high=potential["value"],
                unit="score/100",
                confidence_score=score_confidence,
                confidence_level=_confidence_level(score_confidence),
                method="relative_listing_evidence_score",
                evidence={"not_actual_cvr": True, "available_evidence": potential["explanation"]},
                layer="相对诊断",
            )
        )
    if category_benchmark and category_benchmark.get("percentile") is not None:
        confidence = min(80.0, 35.0 + float(category_benchmark.get("sample_size") or 0) * 2.0)
        score = float(category_benchmark["percentile"])
        estimates.append(
            EstimateValue(
                key="bsr_demand_index",
                label="类目 BSR 需求指数",
                low=score,
                base=score,
                high=score,
                unit="score/100",
                confidence_score=confidence,
                confidence_level=_confidence_level(confidence),
                method="latest_category_bsr_percentile",
                evidence={
                    "category": category_benchmark.get("category"),
                    "sample_size": category_benchmark.get("sample_size"),
                    "bsr_to_sales_model": "disabled",
                },
                layer="相对诊断",
            )
        )
    return estimates


def build_data_quality(
    *,
    product: dict[str, Any],
    snapshots: list[dict[str, Any]],
    offers: list[dict[str, Any]],
    specs: dict[str, Any] | None,
    serp_contexts: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    checks = {
        "搜索商品快照": bool(snapshots),
        "多时点趋势": len(snapshots) >= 2,
        "详情页证据": bool(product.get("detail_collected_at")),
        "报价履约快照": bool(offers),
        "结构化物理规格": bool(specs),
        "关键词市场快照": bool(serp_contexts),
        "卖家精确输入": bool(inputs),
    }
    score = round(sum(checks.values()) / len(checks) * 100, 2)
    latest_time = _datetime_value((snapshots[-1] if snapshots else {}).get("snapshot_at"))
    freshness_days = (datetime.now() - latest_time).days if latest_time else None
    return {
        "score": score,
        "level": _confidence_level(score),
        "checks": checks,
        "missing": [label for label, present in checks.items() if not present],
        "latest_snapshot_age_days": freshness_days,
    }


def _fetch_context(db: MySQLClient, asin: str) -> dict[str, Any]:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, marketplace, asin, title, title_zh, category_path,
                       product_size, date_first_available, detail_collected_at,
                       first_seen_at, last_seen_at, product_url, image_url
                FROM products WHERE asin = %s ORDER BY id ASC LIMIT 1
                """,
                (asin,),
            )
            product_raw = cursor.fetchone()
            if not product_raw:
                return {"product": None}
            product_id = int(product_raw["id"])
            cursor.execute(
                """
                SELECT snapshot_at, price, rating, review_count, monthly_bought,
                       is_deal
                FROM product_snapshots
                WHERE product_id = %s
                ORDER BY snapshot_at ASC, id ASC
                LIMIT 500
                """,
                (product_id,),
            )
            snapshots = cursor.fetchall()
            cursor.execute(
                """
                SELECT snapshot_at, current_price, list_price, currency,
                       discount_percent, coupon_text, availability_status,
                       featured_offer_seller, ships_from, fulfillment_channel,
                       is_prime, offer_count, badges_json, image_count, video_count,
                       bullet_count, has_a_plus, rating_histogram_json, postal_code,
                       source_file, raw_json
                FROM product_offer_snapshots
                WHERE product_id = %s
                ORDER BY snapshot_at ASC, id ASC
                LIMIT 200
                """,
                (product_id,),
            )
            offers = cursor.fetchall()
            cursor.execute("SELECT * FROM product_physical_specs WHERE product_id = %s LIMIT 1", (product_id,))
            physical_specs = cursor.fetchone()
            parent_asin = (physical_specs or {}).get("parent_asin") or asin
            cursor.execute(
                """
                SELECT parent_asin, child_asin, attributes_json, product_url,
                       is_selected, first_seen_at, last_seen_at
                FROM product_variants
                WHERE marketplace = %s AND (parent_asin = %s OR child_asin = %s)
                ORDER BY parent_asin, child_asin
                """,
                (product_raw.get("marketplace") or "US", parent_asin, asin),
            )
            variants = cursor.fetchall()
            cursor.execute(
                """
                SELECT snapshot_at, rank_value, category_name, category_url, is_primary
                FROM product_bsr_snapshots
                WHERE product_id = %s
                ORDER BY snapshot_at ASC, is_primary DESC, rank_value ASC
                LIMIT 500
                """,
                (product_id,),
            )
            bsr_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT id, period_start, period_end, source_type, source_label,
                       sessions, page_views, units_ordered, orders, ordered_sales,
                       featured_offer_percentage, impressions, clicks, cart_adds,
                       purchases, ad_spend, ad_clicks, ad_orders, ad_sales, total_sales,
                       unit_purchase_cost, unit_shipping_cost, unit_fba_fee,
                       unit_referral_fee, unit_other_cost,
                       assumed_cvr_low, assumed_cvr_base, assumed_cvr_high,
                       notes, created_at, updated_at
                FROM product_metric_inputs
                WHERE product_id = %s
                ORDER BY period_end DESC, updated_at DESC, id DESC
                LIMIT 100
                """,
                (product_id,),
            )
            inputs = cursor.fetchall()
            cursor.execute(
                """
                SELECT as_of_date, metric_key, value_low, value_base, value_high,
                       unit, model_version, confidence_score, confidence_level,
                       method, evidence_json, updated_at
                FROM product_estimates
                WHERE product_id = %s
                ORDER BY as_of_date DESC, metric_key ASC
                LIMIT 100
                """,
                (product_id,),
            )
            saved_estimates = cursor.fetchall()
            cursor.execute(
                """
                SELECT DISTINCT k.keyword, ss.snapshot_at, ss.page_count,
                       ss.total_card_count, ss.organic_count, ss.sponsored_count,
                       ss.unique_asin_count, ss.ad_density, ss.price_median,
                       ss.review_median, ss.rating_median, ss.monthly_bought_median,
                       ss.demand_cr3, ss.demand_cr10, ss.data_coverage
                FROM keyword_rank_snapshots kr
                JOIN keywords k ON k.id = kr.keyword_id
                JOIN keyword_serp_snapshots ss
                  ON ss.keyword_id = k.id
                 AND ss.snapshot_at = (
                   SELECT MAX(ss2.snapshot_at) FROM keyword_serp_snapshots ss2 WHERE ss2.keyword_id = k.id
                 )
                WHERE kr.product_id = %s
                ORDER BY ss.snapshot_at DESC, k.keyword ASC
                LIMIT 20
                """,
                (product_id,),
            )
            serp_contexts = cursor.fetchall()
            category_benchmark = _fetch_category_benchmark(cursor, bsr_rows)

    return {
        "product": _normalize_value(product_raw),
        "snapshots": _normalize_value(snapshots),
        "offers": _normalize_value(offers),
        "physical_specs": _normalize_value(physical_specs),
        "variants": _normalize_value(variants),
        "bsr": _normalize_value(bsr_rows),
        "inputs": _normalize_value(inputs),
        "saved_estimates": _normalize_value(saved_estimates),
        "serp_contexts": _normalize_value(serp_contexts),
        "category_benchmark": category_benchmark,
    }


def _fetch_category_benchmark(cursor: Any, bsr_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not bsr_rows:
        return None
    latest_time = max(row["snapshot_at"] for row in bsr_rows)
    latest_primary = next(
        (row for row in reversed(bsr_rows) if row["snapshot_at"] == latest_time and row.get("is_primary")),
        None,
    )
    if latest_primary is None:
        latest_primary = next((row for row in reversed(bsr_rows) if row["snapshot_at"] == latest_time), None)
    if latest_primary is None:
        return None
    category = latest_primary.get("category_name")
    rank_value = _number(latest_primary.get("rank_value"))
    cursor.execute(
        """
        SELECT b.product_id, b.rank_value
        FROM product_bsr_snapshots b
        WHERE b.category_name = %s
          AND b.snapshot_at = (
            SELECT MAX(b2.snapshot_at)
            FROM product_bsr_snapshots b2
            WHERE b2.product_id = b.product_id AND b2.category_name = b.category_name
          )
        """,
        (category,),
    )
    values = [_number(row.get("rank_value")) for row in cursor.fetchall()]
    values = sorted(value for value in values if value is not None)
    if rank_value is None or len(values) < 5:
        return {"category": category, "rank": rank_value, "sample_size": len(values), "percentile": None}
    better_or_equal = sum(value <= rank_value for value in values)
    percentile = round((1 - (better_or_equal - 1) / max(1, len(values) - 1)) * 100, 2)
    return {"category": category, "rank": rank_value, "sample_size": len(values), "percentile": percentile}


def _latest_bsr_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    latest = max(row.get("snapshot_at") for row in rows)
    return [row for row in rows if row.get("snapshot_at") == latest]


def _product_id(cursor: Any, asin: str) -> int | None:
    cursor.execute("SELECT id FROM products WHERE asin = %s ORDER BY id ASC LIMIT 1", (asin,))
    row = cursor.fetchone() or {}
    return int(row["id"]) if row.get("id") is not None else None


def _upsert_metric_input(
    cursor: Any,
    product_id: int,
    normalized: dict[str, Any],
    raw: dict[str, Any],
) -> None:
    columns = ["product_id", "period_start", "period_end", "source_type", *INPUT_COLUMNS, "raw_json"]
    update_columns = [column for column in columns if column not in {"product_id", "period_start", "period_end", "source_type"}]
    placeholders = ", ".join(["%s"] * len(columns))
    assignments = ", ".join(f"{column} = VALUES({column})" for column in update_columns)
    values = [
        product_id,
        normalized["period_start"],
        normalized["period_end"],
        normalized["source_type"],
        *(normalized.get(column) for column in INPUT_COLUMNS),
        json.dumps(raw, ensure_ascii=False, default=str),
    ]
    cursor.execute(
        f"INSERT INTO product_metric_inputs ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {assignments}",
        values,
    )


def _upsert_estimate(cursor: Any, product_id: int, as_of_date: date, estimate: EstimateValue) -> None:
    cursor.execute(
        """
        INSERT INTO product_estimates (
          product_id, as_of_date, metric_key, value_low, value_base, value_high,
          unit, model_version, confidence_score, confidence_level, method, evidence_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          value_low = VALUES(value_low),
          value_base = VALUES(value_base),
          value_high = VALUES(value_high),
          unit = VALUES(unit),
          confidence_score = VALUES(confidence_score),
          confidence_level = VALUES(confidence_level),
          method = VALUES(method),
          evidence_json = VALUES(evidence_json)
        """,
        (
            product_id,
            as_of_date,
            estimate.key,
            estimate.low,
            estimate.base,
            estimate.high,
            estimate.unit,
            estimate.model_version,
            estimate.confidence_score,
            estimate.confidence_level,
            estimate.method,
            json.dumps(estimate.evidence, ensure_ascii=False),
        ),
    )


def _estimate_constructor_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": item["key"],
        "label": item["label"],
        "low": item.get("low"),
        "base": item.get("base"),
        "high": item.get("high"),
        "unit": item["unit"],
        "confidence_score": item["confidence_score"],
        "confidence_level": item["confidence_level"],
        "method": item["method"],
        "evidence": item.get("evidence") or {},
        "model_version": item.get("model_version") or MODEL_VERSION,
        "layer": item.get("layer") or "经验估算",
    }


def _metric_capture_history(
    snapshots: list[dict[str, Any]],
    offers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = build_product_capture_history(snapshots, offers)
    for row in rows:
        row["is_deal"] = _optional_bool(row.get("is_deal"))
    return rows


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"是", "true", "1", "yes"}:
            return True
        if normalized in {"否", "false", "0", "no"}:
            return False
        return None
    return bool(value)


def _current_price(snapshots: list[dict[str, Any]], offers: list[dict[str, Any]]) -> float | None:
    dated: list[tuple[datetime, int, int, float]] = []
    for index, row in enumerate(snapshots):
        value = _number(row.get("price"))
        stamp = _datetime_value(row.get("snapshot_at"))
        if value is not None and stamp is not None:
            dated.append((stamp, 1, index, value))
    for index, row in enumerate(offers):
        value = _number(row.get("current_price"))
        stamp = _datetime_value(row.get("snapshot_at"))
        if value is not None and stamp is not None:
            dated.append((stamp, 0, index, value))
    if dated:
        return max(dated, key=lambda item: item[:3])[3]

    for row in reversed(snapshots):
        value = _number(row.get("price"))
        if value is not None:
            return value
    for row in reversed(offers):
        value = _number(row.get("current_price"))
        if value is not None:
            return value
    return None


def _conversion_potential_score(
    *,
    latest_snapshot: dict[str, Any],
    latest_offer: dict[str, Any] | None,
    specs: dict[str, Any] | None,
) -> tuple[float | None, float]:
    components: list[tuple[float, float]] = []
    rating = _number(latest_snapshot.get("rating"))
    reviews = _number(latest_snapshot.get("review_count"))
    if rating is not None:
        components.append((min(100.0, max(0.0, rating / 5 * 100)), 30.0))
    if reviews is not None:
        components.append((min(100.0, math.log10(reviews + 1) / 5 * 100), 20.0))
    if latest_offer:
        content_signals = [
            min(1.0, (_number(latest_offer.get("image_count")) or 0) / 6),
            min(1.0, (_number(latest_offer.get("bullet_count")) or 0) / 5),
            1.0 if latest_offer.get("has_a_plus") else 0.0,
        ]
        components.append((sum(content_signals) / len(content_signals) * 100, 25.0))
        fulfillment = 0.0
        if latest_offer.get("availability_status") == "in_stock":
            fulfillment += 50.0
        if latest_offer.get("is_prime"):
            fulfillment += 30.0
        if latest_offer.get("fulfillment_channel") in {"FBA", "Amazon Retail"}:
            fulfillment += 20.0
        components.append((fulfillment, 15.0))
        offer_score = 0.0
        offer_score += 50.0 if _number(latest_offer.get("current_price")) is not None else 0.0
        offer_score += 25.0 if latest_offer.get("featured_offer_seller") else 0.0
        offer_score += 25.0 if latest_offer.get("ships_from") else 0.0
        components.append((offer_score, 10.0))
    elif specs:
        components.append((50.0, 5.0))
    total_weight = sum(weight for _, weight in components)
    if total_weight <= 0:
        return None, 0.0
    score = sum(value * weight for value, weight in components) / total_weight
    confidence = min(90.0, 10.0 + total_weight * 0.8)
    return round(score, 2), round(confidence, 2)


def _cvr_scenario(row: dict[str, Any] | None) -> tuple[float, float, float]:
    if row:
        values = tuple(_number(row.get(field)) for field in ("assumed_cvr_low", "assumed_cvr_base", "assumed_cvr_high"))
        if all(value is not None and value > 0 for value in values) and values[0] <= values[1] <= values[2]:
            return values  # type: ignore[return-value]
    return DEFAULT_CVR_SCENARIO


def _per_day_delta(rows: list[dict[str, Any]], field: str) -> float | None:
    usable = [(row, _number(row.get(field)), _datetime_value(row.get("snapshot_at"))) for row in rows]
    usable = [(row, value, stamp) for row, value, stamp in usable if value is not None and stamp is not None]
    if len(usable) < 2:
        return None
    first, last = usable[0], usable[-1]
    days = (last[2] - first[2]).total_seconds() / 86400
    if days <= 0:
        return None
    return (last[1] - first[1]) / days


def _delta(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [_number(row.get(field)) for row in rows]
    values = [value for value in values if value is not None]
    return values[-1] - values[0] if len(values) >= 2 else None


def _safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _safe_percent(numerator: float | None, denominator: float | None) -> float | None:
    value = _safe_divide(numerator, denominator)
    return value * 100 if value is not None else None


def _confidence_level(score: float) -> str:
    if score >= 70:
        return "高"
    if score >= 40:
        return "中"
    return "低"


def _parse_date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise MetricInputError(f"{label}必须是 YYYY-MM-DD。") from exc


def _nonnegative_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise MetricInputError(f"{field} 必须是非负整数。") from exc
    if number < 0 or not number.is_integer():
        raise MetricInputError(f"{field} 必须是非负整数。")
    return int(number)


def _nonnegative_number(value: Any, field: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError) as exc:
        raise MetricInputError(f"{field} 必须是非负数字。") from exc
    if number < 0 or not math.isfinite(number):
        raise MetricInputError(f"{field} 必须是非负数字。")
    return round(number, 6)


def _ratio(value: Any, field: str) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1]
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise MetricInputError(f"{field} 必须是 0-1 比例或百分数。") from exc
    if is_percent or number > 1:
        number /= 100
    if not 0 <= number <= 1:
        raise MetricInputError(f"{field} 必须在 0-1 或 0%-100% 范围内。")
    return round(number, 6)


def _short_text(value: Any, max_length: int) -> str | None:
    text = str(value or "").strip()
    return text[:max_length] if text else None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str) and value and value[0] in "[{":
        try:
            return _normalize_value(json.loads(value))
        except (ValueError, TypeError, json.JSONDecodeError):
            return value
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return value
