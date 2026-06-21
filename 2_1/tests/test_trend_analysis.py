"""services.trend_analysis 的轻量单测。

无需 pytest，可直接运行：
    python tests/test_trend_analysis.py
也兼容 pytest：
    pytest tests/test_trend_analysis.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.trend_analysis import assess_product_trend  # noqa: E402


def test_no_snapshot_is_unjudgeable() -> None:
    result = assess_product_trend([])
    assert result.confidence == "无法判断"
    assert result.growth_score == 50.0
    assert result.sample_size == 0


def test_single_snapshot_is_insufficient() -> None:
    result = assess_product_trend([
        {"snapshot_at": "2026-05-01 17:00:00", "monthly_bought": 1000, "organic_rank": 20},
    ])
    assert result.confidence == "无法判断"
    assert result.growth_score == 50.0
    assert "样本不足" in result.summary


def test_two_snapshots_low_confidence_growth_near_50() -> None:
    # 真实数据典型形态：单商品最多 2 个快照 -> 低置信度，growth 仍贴近 50。
    result = assess_product_trend([
        {"snapshot_at": "2026-05-01 17:00:00", "monthly_bought": 1000, "organic_rank": 40},
        {"snapshot_at": "2026-05-10 17:00:00", "monthly_bought": 1300, "organic_rank": 30},
    ])
    assert result.confidence == "低"
    assert result.sample_size == 2
    # 低置信度(0.30)下，即便需求和排名都好转，growth 也被收敛在 50 附近。
    assert 50.0 < result.growth_score <= 70.0
    assert "置信度：低" in result.summary


def test_high_confidence_rising_demand() -> None:
    rows = [
        {"snapshot_at": f"2026-05-{day:02d} 12:00:00", "monthly_bought": mb, "organic_rank": rk}
        for day, mb, rk in [
            (1, 1000, 50),
            (6, 1300, 42),
            (12, 1700, 35),
            (18, 2200, 28),
            (24, 2800, 20),
        ]
    ]
    result = assess_product_trend(rows)
    assert result.confidence == "高"
    assert result.confidence_score == 0.85
    assert result.growth_score > 70.0  # 强增长 + 高置信度
    demand = next(m for m in result.metrics if m.key == "monthly_bought")
    rank = next(m for m in result.metrics if m.key == "organic_rank")
    assert demand.direction == "上升" and demand.is_improvement is True
    assert rank.direction == "下降" and rank.is_improvement is True  # 排名下降=好转


def test_oscillating_series_is_choppy_not_trend() -> None:
    # n>=3 但上下震荡：净变化为正，但逐步方向不一致 -> 判"震荡"，不当作趋势。
    rows = [
        {"snapshot_at": f"2026-05-{day:02d} 12:00:00", "monthly_bought": mb, "organic_rank": 30}
        for day, mb in [(1, 1000), (6, 1600), (12, 1050), (18, 1700), (24, 1100)]
    ]
    result = assess_product_trend(rows)
    demand = next(m for m in result.metrics if m.key == "monthly_bought")
    assert demand.direction == "震荡"
    assert demand.is_improvement is None


def test_monotonic_series_still_trends_up() -> None:
    # n>=3 且单调上升：一致性=1.0 -> 判"上升"。
    rows = [
        {"snapshot_at": f"2026-05-{day:02d} 12:00:00", "monthly_bought": mb, "organic_rank": 30}
        for day, mb in [(1, 1000), (8, 1300), (16, 1600), (24, 1900)]
    ]
    result = assess_product_trend(rows)
    demand = next(m for m in result.metrics if m.key == "monthly_bought")
    assert demand.direction == "上升" and demand.is_improvement is True


def test_promo_pullup_is_flagged_and_discounted() -> None:
    rows = [
        {"snapshot_at": "2026-05-01 12:00:00", "monthly_bought": 1000, "organic_rank": 40, "is_deal": False},
        {"snapshot_at": "2026-05-08 12:00:00", "monthly_bought": 1500, "organic_rank": 38, "is_deal": False},
        {"snapshot_at": "2026-05-15 12:00:00", "monthly_bought": 2500, "organic_rank": 20, "is_deal": True},
    ]
    result = assess_product_trend(rows)
    assert result.promo_warning is not None
    assert "促销" in result.summary


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
