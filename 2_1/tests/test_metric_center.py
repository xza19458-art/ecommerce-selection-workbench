from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.metric_center import (
    MetricInputError,
    _current_price,
    _metric_capture_history,
    build_product_estimates,
    calculate_deterministic_metrics,
    calculate_exact_metrics,
    normalize_metric_input,
)


def _by_key(rows: list[dict], key: str) -> dict:
    return next(row for row in rows if row["key"] == key)


def test_metric_input_normalizes_percentages_and_numbers() -> None:
    result = normalize_metric_input(
        {
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
            "source_type": "manual",
            "sessions": "1,000",
            "ordered_sales": "$2,500.50",
            "featured_offer_percentage": "95%",
            "assumed_cvr_low": "3%",
            "assumed_cvr_base": 8,
            "assumed_cvr_high": 0.15,
        }
    )

    assert result["sessions"] == 1000
    assert result["ordered_sales"] == 2500.5
    assert result["featured_offer_percentage"] == 0.95
    assert result["assumed_cvr_low"] == 0.03
    assert result["assumed_cvr_base"] == 0.08
    assert result["assumed_cvr_high"] == 0.15


def test_metric_input_rejects_partial_or_reversed_scenario() -> None:
    try:
        normalize_metric_input(
            {
                "period_start": "2026-07-01",
                "period_end": "2026-07-31",
                "sessions": 100,
                "assumed_cvr_low": "3%",
            }
        )
    except MetricInputError as exc:
        assert "同时填写" in str(exc)
    else:
        raise AssertionError("partial scenario should be rejected")


def test_exact_metrics_keep_conversion_definitions_separate() -> None:
    row = normalize_metric_input(
        {
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
            "source_type": "sp_api",
            "sessions": 1000,
            "units_ordered": 100,
            "orders": 80,
            "ordered_sales": 1000,
            "impressions": 10000,
            "clicks": 500,
            "cart_adds": 200,
            "purchases": 100,
            "ad_spend": 50,
            "ad_clicks": 100,
            "ad_orders": 10,
            "ad_sales": 500,
            "total_sales": 1000,
            "unit_purchase_cost": 2,
            "unit_shipping_cost": 1,
            "unit_fba_fee": 2,
            "unit_referral_fee": 1,
        }
    )

    metrics = calculate_exact_metrics(row, current_price=10)

    assert _by_key(metrics, "unit_session_percentage")["value"] == 10.0
    assert _by_key(metrics, "order_conversion_rate")["value"] == 8.0
    assert _by_key(metrics, "ctr")["value"] == 5.0
    assert _by_key(metrics, "ad_conversion_rate")["value"] == 10.0
    assert _by_key(metrics, "cpc")["value"] == 0.5
    assert _by_key(metrics, "acos")["value"] == 10.0
    assert _by_key(metrics, "roas")["value"] == 10.0
    assert _by_key(metrics, "tacos")["value"] == 5.0
    assert _by_key(metrics, "unit_contribution_before_ads")["value"] == 4.0
    assert _by_key(metrics, "max_cpc_exact")["value"] == 0.4
    assert _by_key(metrics, "unit_session_percentage")["layer"] == "官方输入"


def test_experience_scenario_only_derives_required_sessions() -> None:
    product = {"asin": "B000000001", "first_seen_at": "2026-07-01 00:00:00"}
    snapshots = [
        {
            "snapshot_at": "2026-07-12 00:00:00",
            "price": 20,
            "rating": 4.5,
            "review_count": 100,
            "monthly_bought": 120,
            "is_deal": False,
        }
    ]
    deterministic = calculate_deterministic_metrics(
        product=product,
        snapshots=snapshots,
        offers=[],
        bsr_rows=[],
        specs=None,
        variants=[],
        category_benchmark=None,
    )
    estimates = build_product_estimates(
        product=product,
        snapshots=snapshots,
        offers=[],
        specs=None,
        latest_input=None,
        deterministic_metrics=deterministic,
        category_benchmark=None,
    )
    rows = [item.to_dict() for item in estimates]

    scenario = _by_key(rows, "assumed_cvr")
    sessions = _by_key(rows, "required_monthly_sessions_for_demand_floor")
    assert (scenario["low"], scenario["base"], scenario["high"]) == (0.03, 0.08, 0.15)
    assert sessions["low"] == 800.0
    assert sessions["base"] == 1500.0
    assert sessions["high"] == 4000.0
    assert sessions["confidence_level"] == "低"
    assert "真实 Sessions" in sessions["evidence"]["limitation"]


def test_metric_capture_history_uses_newer_detail_observation_without_rank_copy() -> None:
    history = _metric_capture_history(
        [
            {
                "snapshot_at": "2026-07-13 12:00:00",
                "price": 16.59,
                "rating": 4.6,
                "review_count": 31,
                "monthly_bought": 500,
                "organic_rank": 34,
                "is_deal": 0,
            }
        ],
        [
            {
                "snapshot_at": "2026-07-29 22:55:51",
                "current_price": 15.99,
                "raw_json": {
                    "detail_page_metrics": {
                        "rating": 4.7,
                        "review_count": 37,
                        "monthly_bought": 400,
                        "is_deal": True,
                    }
                },
            }
        ],
    )

    assert history[-1]["source_type"] == "detail"
    assert history[-1]["monthly_bought"] == 400
    assert history[-1]["organic_rank"] is None
    assert history[-1]["is_deal"] is True


def test_current_price_uses_newest_observation_across_sources() -> None:
    snapshots = [
        {"snapshot_at": "2026-07-20 12:00:00", "price": 12.5},
    ]
    older_offer = [
        {"snapshot_at": "2026-07-19 12:00:00", "current_price": 11.0},
    ]
    newer_offer = [
        {"snapshot_at": "2026-07-21 12:00:00", "current_price": 10.0},
    ]

    assert _current_price(snapshots, older_offer) == 12.5
    assert _current_price(snapshots, newer_offer) == 10.0


if __name__ == "__main__":
    tests = [
        test_metric_input_normalizes_percentages_and_numbers,
        test_metric_input_rejects_partial_or_reversed_scenario,
        test_exact_metrics_keep_conversion_definitions_separate,
        test_experience_scenario_only_derives_required_sessions,
        test_metric_capture_history_uses_newer_detail_observation_without_rank_copy,
        test_current_price_uses_newest_observation_across_sources,
    ]
    for test in tests:
        test()
    print(f"metric center tests passed: {len(tests)}/{len(tests)}")
