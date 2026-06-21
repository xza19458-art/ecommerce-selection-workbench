"""商品多周期快照的趋势置信度分析。

设计目标（见 decisions/2026-06-16-趋势置信度算法.md）：
- 置信度优先：样本不足时诚实输出"低置信度/样本不足"，不把单次波动当趋势。
- 可解释：输出中文摘要，能向非技术决策者解释"为什么这是/不是趋势"。
- 安全泛化：growth_score 在低置信度时收敛到 50（与现有占位一致）。

本模块为纯函数实现，仅依赖标准库，可独立单测，不读数据库、不改评分模型。
输入快照字典字段沿用现有契约（Storage 已确认一致）：
    snapshot_at, price, rating, review_count, monthly_bought, organic_rank, is_deal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence


# 时间跨度阈值（天）
_SPAN_MID = 7.0
_SPAN_HIGH = 14.0

# 用于增长信号归一化的"显著变化"基准（相对比例）
_GROWTH_FULL_SCALE = 0.5  # 月购买量/排名相对改善达到 50% 视为满格信号


@dataclass(frozen=True)
class MetricTrend:
    """单个指标的首尾趋势描述。"""

    label: str
    key: str
    start: float | None
    end: float | None
    direction: str  # 上升 / 下降 / 稳定 / 数据不足
    change_ratio: float | None  # 相对变化，(end-start)/|start|；start 为 0 或缺失时为 None
    is_improvement: bool | None  # 对该指标而言是否算好转；中性指标为 None


@dataclass(frozen=True)
class TrendAssessment:
    """一个商品的整体趋势评估结果。"""

    sample_size: int
    span_days: float
    confidence: str  # 无法判断 / 低 / 中 / 高
    confidence_score: float  # 0..1
    growth_score: float  # 0..100，可用于替换 scoring 的 growth 占位
    metrics: list[MetricTrend] = field(default_factory=list)
    promo_warning: str | None = None
    summary: str = ""


# 指标定义：key -> (中文名, 好转方向)。好转方向 +1 表示值增大为好转，
# -1 表示值减小为好转（如自然排名），0 表示中性（不计入好坏）。
_METRICS: tuple[tuple[str, str, int], ...] = (
    ("monthly_bought", "近月购买量", 1),
    ("organic_rank", "自然排名", -1),
    ("price", "价格", 0),
    ("rating", "评分", 1),
    ("review_count", "评论数", 0),
)


def assess_product_trend(snapshots: Iterable[dict[str, Any]]) -> TrendAssessment:
    """评估单个商品的多周期快照趋势。

    Args:
        snapshots: 同一商品的快照字典序列，需含 snapshot_at 及指标字段。
            会按 snapshot_at 升序排序并按时间点去重。

    Returns:
        TrendAssessment：含样本量、时间跨度、置信度、growth_score、
        逐指标趋势、促销提示与中文摘要。

    假设：
        - 输入均属同一商品（调用方负责按 ASIN 分组）。
        - 单个快照只有 1 条时无法判断趋势，置信度为"无法判断"。
    """

    rows = _dedupe_sorted(snapshots)
    n = len(rows)
    span_days = _span_days(rows)

    if n < 2:
        return TrendAssessment(
            sample_size=n,
            span_days=span_days,
            confidence="无法判断",
            confidence_score=0.0,
            growth_score=50.0,
            metrics=[],
            promo_warning=None,
            summary=_summary_insufficient(n),
        )

    confidence, confidence_score = _confidence(n, span_days)
    metrics = [_metric_trend(label, key, good_dir, rows) for key, label, good_dir in _METRICS]

    growth_score, raw_growth = _growth_score(metrics, confidence_score)
    promo_warning = _promo_warning(rows, metrics)
    if promo_warning and raw_growth > 0:
        # 疑似促销拉升时，对正向增长打折，避免把短期促销当成长期趋势。
        growth_score = round(50.0 + (growth_score - 50.0) * 0.5, 2)

    summary = _summary(n, span_days, confidence, metrics, promo_warning)

    return TrendAssessment(
        sample_size=n,
        span_days=span_days,
        confidence=confidence,
        confidence_score=round(confidence_score, 3),
        growth_score=round(growth_score, 2),
        metrics=metrics,
        promo_warning=promo_warning,
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# 内部辅助
# --------------------------------------------------------------------------- #


def _dedupe_sorted(snapshots: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 snapshot_at 升序排序并按时间点去重（保留最后一条）。"""
    valid = [row for row in snapshots if row]
    valid.sort(key=lambda row: _sort_key(row.get("snapshot_at")))
    deduped: dict[str, dict[str, Any]] = {}
    for row in valid:
        deduped[str(row.get("snapshot_at"))] = row
    return list(deduped.values())


def _sort_key(value: Any) -> str:
    dt = _to_datetime(value)
    if dt is not None:
        return dt.isoformat()
    return str(value or "")


def _confidence(n: int, span_days: float) -> tuple[str, float]:
    """根据样本量与时间跨度给出置信度分级。短窗封顶为'低'。"""
    if n == 2:
        return "低", 0.30
    if n <= 4:
        if span_days < _SPAN_MID:
            return "低", 0.30
        return "中", 0.60
    # n >= 5
    if span_days < _SPAN_MID:
        return "低", 0.30
    if span_days < _SPAN_HIGH:
        return "中", 0.60
    return "高", 0.85


def _metric_trend(label: str, key: str, good_dir: int, rows: list[dict[str, Any]]) -> MetricTrend:
    series = [_to_number(row.get(key)) for row in rows]
    present = [v for v in series if v is not None]
    if len(present) < 2:
        return MetricTrend(label, key, None, None, "数据不足", None, None)

    start = present[0]
    end = present[-1]
    delta = end - start
    change_ratio = (delta / abs(start)) if start not in (0, None) else None

    direction = _direction(present, good_dir)
    is_improvement: bool | None
    if good_dir == 0 or direction in ("稳定", "震荡"):
        is_improvement = None
    else:
        # good_dir=+1：值增大为好；good_dir=-1：值减小为好。
        is_improvement = (delta * good_dir) > 0

    return MetricTrend(label, key, start, end, direction, change_ratio, is_improvement)


def _direction(present: list[float], good_dir: int) -> str:
    """判断指标方向。

    n<3 时仅首尾对比；n>=3 时增加单调一致性校验：逐步方向与总体方向
    一致的比例 >=0.6 才判为上升/下降，否则判为"震荡"，避免把波动当趋势。
    """
    start, end = present[0], present[-1]
    delta = end - start
    if _is_stable(start, delta):
        return "稳定"
    overall = "上升" if delta > 0 else "下降"
    if len(present) < 3:
        return overall
    steps = [present[i + 1] - present[i] for i in range(len(present) - 1)]
    same = sum(1 for s in steps if abs(s) > 1e-9 and (s > 0) == (delta > 0))
    consistency = same / len(steps) if steps else 0.0
    return overall if consistency >= 0.6 else "震荡"


def _is_stable(start: float, delta: float) -> bool:
    """稳定阈值：相对变化 < 1%（绝对值大的指标用绝对阈值兜底）。"""
    if abs(delta) < 1e-9:
        return True
    if start != 0 and abs(delta) / abs(start) < 0.01:
        return True
    if abs(delta) < 1 and abs(start) >= 100:
        return True
    return False


def _growth_score(metrics: list[MetricTrend], confidence_score: float) -> tuple[float, float]:
    """由月购买量与自然排名的改善程度计算增长分。

    返回 (growth_score, raw_growth)。raw_growth ∈ [-1, 1]，
    growth_score = clamp(50 + raw_growth * 50 * confidence_score, 0, 100)。
    低置信度时收敛到 50，与现有占位一致。
    """
    signals: list[float] = []
    by_key = {m.key: m for m in metrics}

    demand = by_key.get("monthly_bought")
    if demand and demand.change_ratio is not None:
        signals.append(_clamp_unit(demand.change_ratio / _GROWTH_FULL_SCALE))

    rank = by_key.get("organic_rank")
    if rank and rank.change_ratio is not None:
        # 排名相对下降（change_ratio 为负）= 好转，取反号成为正信号。
        signals.append(_clamp_unit(-rank.change_ratio / _GROWTH_FULL_SCALE))

    raw_growth = sum(signals) / len(signals) if signals else 0.0
    raw_growth = _clamp_unit(raw_growth)
    growth_score = _clamp(50.0 + raw_growth * 50.0 * confidence_score, 0.0, 100.0)
    return growth_score, raw_growth


def _promo_warning(rows: list[dict[str, Any]], metrics: list[MetricTrend]) -> str | None:
    """最新快照在促销且短期需求/排名大幅改善时，提示疑似促销拉升。"""
    if not rows:
        return None
    if not _truthy(rows[-1].get("is_deal")):
        return None
    by_key = {m.key: m for m in metrics}
    demand = by_key.get("monthly_bought")
    rank = by_key.get("organic_rank")
    sharp_demand = bool(demand and demand.change_ratio is not None and demand.change_ratio >= 0.30)
    sharp_rank = bool(rank and rank.change_ratio is not None and rank.change_ratio <= -0.30)
    if sharp_demand or sharp_rank:
        return "最新快照处于促销，且需求/排名短期大幅改善，疑似短期促销拉升，需观察促销结束后是否回落。"
    return None


# --------------------------------------------------------------------------- #
# 中文摘要
# --------------------------------------------------------------------------- #


def _summary_insufficient(n: int) -> str:
    if n <= 0:
        return "暂无历史快照，无法判断趋势（样本不足）。"
    return "当前仅有 1 条采集记录，无法判断趋势（样本不足，建议继续积累快照）。"


def _summary(
    n: int,
    span_days: float,
    confidence: str,
    metrics: list[MetricTrend],
    promo_warning: str | None,
) -> str:
    head = f"基于 {n} 个快照、跨度约 {span_days:.0f} 天，趋势置信度：{confidence}。"
    moves = [
        f"{m.label} {m.start:g}→{m.end:g}（{m.direction}{_improve_tag(m)}）"
        for m in metrics
        if m.direction not in ("稳定", "数据不足")
    ]
    body = ("主要变化：" + "；".join(moves) + "。") if moves else "各指标基本稳定或数据不足。"
    caveat = "（未做季节性校正）"
    if confidence in ("无法判断", "低"):
        caveat = "（样本偏少，结论仅供观察，未做季节性校正）"
    tail = (" " + promo_warning) if promo_warning else ""
    return head + body + caveat + tail


def _improve_tag(metric: MetricTrend) -> str:
    if metric.is_improvement is True:
        return "，好转"
    if metric.is_improvement is False:
        return "，转差"
    return ""


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #


def _span_days(rows: Sequence[dict[str, Any]]) -> float:
    times = [_to_datetime(row.get("snapshot_at")) for row in rows]
    times = [t for t in times if t is not None]
    if len(times) < 2:
        return 0.0
    return (max(times) - min(times)).total_seconds() / 86400.0


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    for ch in ("$", "%"):
        text = text.replace(ch, "")
    try:
        return float(text)
    except ValueError:
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "deal", "是")
    return bool(value)


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _clamp_unit(value: float) -> float:
    return _clamp(value, -1.0, 1.0)
