from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.scoring_v2 import (  # noqa: E402
    CALIBRATION_VERSION,
    MODEL_VERSION,
    ScoringV2Error,
    _calibration_flags,
    _context_history,
    _rank_confidence_from_keyword_rank,
    _sort_rows,
    build_scoring_v2_calibration,
    get_scoring_v2_model,
    load_product_scoring_evidence,
    score_shadow_context,
)
from services.scoring_calibration_export import (  # noqa: E402
    EXPORT_SCHEMA_VERSION,
    ScoringCalibrationExportError,
    export_scoring_calibration,
    load_exported_scoring_calibration_reviews,
)


AS_OF = datetime(2026, 7, 13, 12, 0, 0)


def test_rank_confidence_comes_from_keyword_rank_context() -> None:
    assert _rank_confidence_from_keyword_rank({"page_no": 1, "organic_rank": 8, "is_sponsored": 0}) == "page_first"
    assert _rank_confidence_from_keyword_rank({"page_no": 3, "organic_rank": 95, "is_sponsored": 0}) == "batch_continuous"
    assert _rank_confidence_from_keyword_rank({"page_no": 1, "organic_rank": None, "is_sponsored": 1}) == "sponsored"
    assert _rank_confidence_from_keyword_rank({"page_no": 2, "organic_rank": None, "is_sponsored": 0}, fallback="batch_continuous") == "unknown"


def _context(**overrides):
    row = {
        "product_id": 1,
        "marketplace": "US",
        "asin": "B000000001",
        "title": "Example Product",
        "keyword_id": 7,
        "keyword": "example keyword",
        "rank_snapshot_id": 70,
        "product_snapshot_id": 80,
        "snapshot_at": "2026-07-13 10:00:00",
        "price": 22.0,
        "rating": 4.6,
        "review_count": 120,
        "monthly_bought": 1000,
        "organic_rank": 12,
        "rank_confidence": "batch_continuous",
        "is_deal": False,
        "is_sponsored": False,
        "legacy_total_score": 68.0,
        "legacy_growth_score": 50.0,
    }
    row.update(overrides)
    return row


def _history(*, monthly=(1000,), ranks=(12,), deal=False):
    rows = []
    for index, monthly_value in enumerate(monthly):
        rows.append(
            {
                "snapshot_id": 80 + index,
                "snapshot_at": f"2026-07-{1 + index * 6:02d} 10:00:00",
                "price": 22.0,
                "rating": 4.6,
                "review_count": 120 + index * 10,
                "monthly_bought": monthly_value,
                "organic_rank": 999 - index,
                "is_deal": deal and index == len(monthly) - 1,
            }
        )
    rank_rows = [
        {
            "rank_snapshot_id": 70 + index,
            "snapshot_at": row["snapshot_at"],
            "organic_rank": ranks[index] if index < len(ranks) else None,
            "is_sponsored": False,
        }
        for index, row in enumerate(rows)
    ]
    return rows, rank_rows


def test_missing_values_are_neutral_not_fake_zero() -> None:
    history, _ = _history(monthly=(None,), ranks=(None,))
    result = score_shadow_context(
        _context(monthly_bought=None, organic_rank=None, rank_confidence="unknown"),
        history,
        [],
        as_of=AS_OF,
    )
    components = {item["key"]: item for item in result["opportunity_components"]}
    assert components["demand"]["score"] == 50.0
    assert components["demand"]["status"] == "neutral_missing"
    assert components["visibility"]["score"] == 50.0
    assert result["confidence_score"] < 55
    assert result["recommendation_code"] == "pause"


def test_product_scoring_evidence_falls_back_without_faking_keyword_rank(monkeypatch) -> None:
    import services.scoring_v2 as scoring_v2

    fallback = _context(
        keyword_id=None,
        keyword=None,
        rank_snapshot_id=None,
        organic_rank=None,
        rank_confidence="unknown",
        context_scope="product_only",
    )
    product_history, _ = _history(monthly=(1000,), ranks=(None,))
    monkeypatch.setattr(scoring_v2, "_load_replay_evidence", lambda *_args: ([], {}, {}))
    monkeypatch.setattr(
        scoring_v2,
        "_load_product_only_evidence",
        lambda *_args: (fallback, product_history),
    )

    evidence = load_product_scoring_evidence("B000000001", client=object())

    assert evidence["context"]["keyword_id"] is None
    assert evidence["context"]["organic_rank"] is None
    assert evidence["keyword_rank_history"] == []
    assert evidence["available_contexts"][0]["context_scope"] == "product_only"
    assert "自然序位保持未知" in evidence["selection_warning"]


def test_explicit_keyword_never_uses_product_only_fallback(monkeypatch) -> None:
    import services.scoring_v2 as scoring_v2

    fallback_calls: list[bool] = []
    monkeypatch.setattr(scoring_v2, "_load_replay_evidence", lambda *_args: ([], {}, {}))
    monkeypatch.setattr(
        scoring_v2,
        "_load_product_only_evidence",
        lambda *_args: fallback_calls.append(True),
    )

    with pytest.raises(ScoringV2Error, match="关键词"):
        load_product_scoring_evidence(
            "B000000001",
            keyword="missing keyword",
            client=object(),
        )
    assert fallback_calls == []


def test_single_snapshot_never_claims_growth() -> None:
    history, ranks = _history(monthly=(1000,), ranks=(12,))
    result = score_shadow_context(_context(), history, ranks, as_of=AS_OF)
    growth = next(item for item in result["opportunity_components"] if item["key"] == "growth")
    assert growth["score"] == 50.0
    assert growth["status"] == "neutral_missing"
    assert result["trend"]["signal_point_count"] == 1
    assert result["trend"]["growth_score"] == 50.0


def test_high_demand_head_product_is_benchmark_not_direct_entry() -> None:
    context = _context(
        price=22.0,
        rating=4.7,
        review_count=234_800,
        monthly_bought=70_000,
        organic_rank=4,
    )
    history, ranks = _history(monthly=(70_000,), ranks=(4,))
    result = score_shadow_context(context, history, ranks, as_of=AS_OF)
    assert result["opportunity_score"] >= 75
    assert result["risk_score"] >= 60
    assert result["recommendation_code"] == "benchmark_only"
    assert "对标" in result["recommendation_reason"]


def test_trend_strategy_blocks_low_confidence_growth() -> None:
    history, ranks = _history(monthly=(1000, 1500), ranks=(20, 12))
    result = score_shadow_context(_context(), history, ranks, strategy="trend", as_of=AS_OF)
    assert result["trend"]["confidence_score"] == 0.3
    assert result["recommendation_code"] == "pause"
    assert "趋势型策略" in result["recommendation_reason"]


def test_rank_trend_only_uses_same_keyword_history() -> None:
    product_history, _ = _history(monthly=(1000, 1000), ranks=(80, 10))
    merged_without_keyword_ranks = _context_history(product_history, [], _context())
    assert all(row["organic_rank"] is None for row in merged_without_keyword_ranks)

    _, keyword_ranks = _history(monthly=(1000, 1000), ranks=(80, 10))
    merged = _context_history(product_history, keyword_ranks, _context())
    assert [row["organic_rank"] for row in merged] == [80, 10]


def test_strategies_change_weights_but_preserve_source_identity() -> None:
    history, ranks = _history(monthly=(600, 1000, 1400), ranks=(30, 20, 12))
    balanced = score_shadow_context(_context(), history, ranks, strategy="balanced", as_of=AS_OF)
    differentiation = score_shadow_context(_context(), history, ranks, strategy="differentiation", as_of=AS_OF)
    assert balanced["opportunity_score"] != differentiation["opportunity_score"]
    assert balanced["source_identity"]["model_version"] == MODEL_VERSION
    assert differentiation["source_identity"]["strategy"] == "differentiation"
    assert balanced["source_identity"]["writes_production_score"] is False


def test_global_sort_happens_before_page_slice() -> None:
    rows = [
        {"asin": "B000000001", "opportunity_score": 20, "confidence_score": 90},
        {"asin": "B000000002", "opportunity_score": 90, "confidence_score": 40},
        {"asin": "B000000003", "opportunity_score": 60, "confidence_score": 70},
    ]
    ordered = _sort_rows(rows, "opportunity_score", "desc")
    assert [row["asin"] for row in ordered] == ["B000000002", "B000000003", "B000000001"]
    assert ordered[:2][-1]["opportunity_score"] == 60


def test_shadow_service_has_no_score_writes() -> None:
    source = (ROOT / "services" / "scoring_v2.py").read_text(encoding="utf-8")
    decision = (ROOT.parent / "decisions" / "2026-07-13-趋势与评分模型V2影子回放.md").read_text(encoding="utf-8")
    assert "INSERT INTO product_scores" not in source
    assert "UPDATE product_scores" not in source
    assert "不修改 `analysis/scoring.py`" in decision
    model = get_scoring_v2_model()
    assert model["status"] == "shadow_readonly"
    assert model["version"] == MODEL_VERSION


def _calibration_row(
    product_id: int,
    keyword_id: int,
    *,
    asin: str,
    recommendation: str,
    opportunity: float,
    risk: float,
    confidence: float,
) -> dict:
    labels = {
        "priority_validate": "优先人工验证",
        "observe": "可进入观察池",
        "benchmark_only": "仅适合作为对标",
        "pause": "暂缓",
    }
    return {
        "product_id": product_id,
        "keyword_id": keyword_id,
        "asin": asin,
        "keyword": f"keyword-{keyword_id}",
        "title": f"Product {asin}",
        "price": 19.99,
        "rating": 4.5,
        "review_count": 100,
        "monthly_bought": 1000,
        "organic_rank": 20,
        "rank_confidence": "batch_continuous",
        "opportunity_score": opportunity,
        "risk_score": risk,
        "confidence_score": confidence,
        "recommendation_code": recommendation,
        "recommendation_label": labels[recommendation],
        "trend": {
            "sample_size": 3,
            "signal_point_count": 3,
            "span_days": 35,
            "promo_warning": None,
        },
    }


def test_calibration_flags_surface_strategy_and_evidence_risks() -> None:
    row = _calibration_row(
        1,
        7,
        asin="B000000001",
        recommendation="benchmark_only",
        opportunity=74,
        risk=64,
        confidence=50,
    )
    row.update(
        {
            "monthly_bought": None,
            "organic_rank": None,
            "rank_confidence": "unknown",
            "trend": {
                "sample_size": 1,
                "signal_point_count": 1,
                "span_days": 0,
                "promo_warning": "促销期出现短期拉升",
            },
        }
    )
    outcomes = {
        "balanced": {"recommendation_code": "benchmark_only", "recommendation_label": "仅适合作为对标"},
        "trend": {"recommendation_code": "pause", "recommendation_label": "暂缓"},
    }
    flags = _calibration_flags(row, outcomes, asin_context_count=2)
    codes = {flag["code"] for flag in flags}
    assert {
        "strategy_disagreement",
        "single_snapshot",
        "low_confidence_high_opportunity",
        "price_uncalibrated",
        "rank_evidence_gap",
        "missing_core_fields",
        "multi_keyword_context",
        "high_opportunity_high_risk",
        "promotion_sensitive",
    }.issubset(codes)


def test_calibration_sampling_is_stable_and_avoids_duplicate_asins() -> None:
    balanced = [
        _calibration_row(1, 1, asin="B000000001", recommendation="observe", opportunity=61, risk=40, confidence=80),
        _calibration_row(1, 2, asin="B000000001", recommendation="observe", opportunity=72, risk=42, confidence=80),
        _calibration_row(2, 1, asin="B000000002", recommendation="observe", opportunity=68, risk=41, confidence=80),
        _calibration_row(3, 1, asin="B000000003", recommendation="pause", opportunity=48, risk=55, confidence=50),
        _calibration_row(4, 1, asin="B000000004", recommendation="pause", opportunity=56, risk=58, confidence=50),
    ]
    strategy_rows = {code: [dict(row) for row in balanced] for code in ("balanced", "low_budget", "differentiation", "trend")}
    strategy_rows["trend"][2]["recommendation_code"] = "pause"
    strategy_rows["trend"][2]["recommendation_label"] = "暂缓"

    first = build_scoring_v2_calibration(strategy_rows, strategy="balanced", sample_per_bucket=2)
    second = build_scoring_v2_calibration(strategy_rows, strategy="balanced", sample_per_bucket=2)
    first_keys = [row["sample_key"] for row in first["rows"]]
    second_keys = [row["sample_key"] for row in second["rows"]]
    assert first_keys == second_keys
    assert len({row["asin"] for row in first["rows"]}) == len(first["rows"])
    assert first["summary"]["multi_context_asin_count"] == 1
    assert first["summary"]["strategy_disagreement_count"] == 1
    assert first["summary"]["strategy_changes"]["trend"] == 1
    assert CALIBRATION_VERSION.endswith("calibration-v1")


def test_calibration_sampling_seed_rotates_and_excludes_reviewed_asins() -> None:
    balanced = [
        _calibration_row(
            product_id,
            1,
            asin=f"B{product_id:09d}",
            recommendation="observe",
            opportunity=40 + product_id,
            risk=35 + product_id / 4,
            confidence=80,
        )
        for product_id in range(1, 31)
    ]
    strategy_rows = {
        code: [dict(row) for row in balanced]
        for code in ("balanced", "low_budget", "differentiation", "trend")
    }

    first = build_scoring_v2_calibration(
        strategy_rows,
        strategy="balanced",
        sample_per_bucket=3,
        sample_seed="seed-alpha",
    )
    repeated = build_scoring_v2_calibration(
        strategy_rows,
        strategy="balanced",
        sample_per_bucket=3,
        sample_seed="seed-alpha",
    )
    rotated = build_scoring_v2_calibration(
        strategy_rows,
        strategy="balanced",
        sample_per_bucket=3,
        sample_seed="seed-beta",
    )
    first_keys = [row["sample_key"] for row in first["rows"]]
    assert first_keys == [row["sample_key"] for row in repeated["rows"]]
    assert set(first_keys) != {row["sample_key"] for row in rotated["rows"]}

    excluded_asins = [first["rows"][0]["asin"], first["rows"][1]["asin"]]
    excluded = build_scoring_v2_calibration(
        strategy_rows,
        strategy="balanced",
        sample_per_bucket=3,
        sample_seed="seed-gamma",
        exclude_asins=excluded_asins,
    )
    assert not ({row["asin"] for row in excluded["rows"]} & set(excluded_asins))
    assert excluded["summary"]["source_context_count"] == 30
    assert excluded["summary"]["context_count"] == 28
    assert excluded["sampling"]["excluded_asin_count"] == 2
    assert excluded["sampling"]["method"] == "seeded_quantile_v1"


def _calibration_export_payload(
    *,
    judgment: str = "reasonable",
    note: str = "结论与证据一致",
    export_scope: str = "reviewed",
) -> dict:
    model = get_scoring_v2_model()
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "export_type": "scoring_v2_shadow_calibration",
        "dataset_purpose": "model_calibration_labels" if export_scope == "reviewed" else "audit_snapshot",
        "exported_at": "2026-07-15T10:00:00.000Z",
        "model_spec": {
            "version": MODEL_VERSION,
            "scope": model["scope"],
            "rank_basis": model["rank_basis"],
            "primary_strategy": "balanced",
            "strategies": {
                item["code"]: {
                    "opportunity_weights": item["opportunity_weights"],
                    "risk_weights": item["risk_weights"],
                }
                for item in model["strategies"]
            },
            "recommendation_labels": model["recommendations"],
            "missing_value_policy": model["missing_value_policy"],
        },
        "calibration_spec": {
            "version": CALIBRATION_VERSION,
            "label_definitions": [{"code": "reasonable", "label": "结论合理"}],
            "audit_flag_labels": {
                "single_snapshot": {"label": "单点趋势", "severity": "attention"}
            },
        },
        "selection": {
            "marketplace": "US",
            "keyword_filter": "",
            "sample_per_bucket": 2,
            "export_scope": export_scope,
            "source_sample_count": 1,
            "review_pool_count": 1,
            "bucket_count": 1,
            "sample_generated_at": "2026-07-15 18:00:00",
            "sample_seed": "seed-fixture",
            "sampling_method": "seeded_quantile_v1",
            "excluded_asins": ["B000000099"],
            "excluded_asin_count": 2,
            "batch_number": 3,
        },
        "samples": [
            {
                "sample_key": "model:balanced:7:1",
                "sample_seed": "seed-original-batch",
                "product_id": 1,
                "asin": "b000000001",
                "title": "示例商品",
                "keyword": {"id": 7, "text": "示例关键词"},
                "stratum": {"code": "observe:high", "label": "观察 · 高置信"},
                "observed_at": "2026-07-15 17:00:00",
                "model_inputs": {
                    "price": 22.0,
                    "rating": 4.6,
                    "review_count": 120,
                    "monthly_bought": 1000,
                    "organic_rank": 12,
                    "rank_confidence": "batch_continuous",
                    "is_deal": False,
                    "verbose_reason": "不应进入校准数据集",
                },
                "trend_features": {
                    "sample_size": 3,
                    "signal_point_count": 3,
                    "span_days": 14.0,
                    "confidence_score": 0.8,
                    "growth_score": 64.0,
                    "promotion_warning": False,
                    "summary": "不导出页面说明",
                    "metrics": {
                        "monthly_bought": {
                            "start": 700,
                            "end": 1000,
                            "change_ratio": 0.4286,
                            "direction": "上升",
                        }
                    },
                },
                "component_scores": {
                    "opportunity": {
                        "demand": {"score": 74.0, "weight": 0.35, "status": "observed", "reason": "剔除"}
                    },
                    "risk": {
                        "review_barrier": {"score": 36.0, "weight": 0.35, "status": "observed"}
                    },
                    "confidence": {
                        "field_completeness": {"score": 100.0, "weight": 0.4, "status": "observed"}
                    },
                },
                "primary_output": {
                    "opportunity_score": 68.0,
                    "risk_score": 42.0,
                    "confidence_score": 80.0,
                    "legacy_total_score": 65.0,
                    "recommendation_code": "observe",
                    "recommendation_reason": "不导出页面文案",
                },
                "strategy_outputs": {
                    "balanced": {
                        "opportunity_score": 68.0,
                        "risk_score": 42.0,
                        "confidence_score": 80.0,
                        "recommendation_code": "observe",
                        "recommendation_label": "观察",
                    }
                },
                "audit_flag_codes": ["single_snapshot"],
                "evidence_ids": {
                    "rank_snapshot_id": 70,
                    "product_snapshot_id": 80,
                    "history_snapshot_ids": [80, 81, 82],
                    "keyword_rank_snapshot_ids": [70, 71, 72],
                },
                "human_label": {
                    "judgment": judgment,
                    "note": note,
                    "reviewed_at": "2026-07-15T10:00:00.000Z",
                },
                "page_only_noise": {"reasons": ["这一整块都不应导出"]},
            }
        ],
        "summary": {"page_only_count": 999},
    }


def test_calibration_export_writes_valid_json_to_fixed_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        payload = _calibration_export_payload()
        payload["model_spec"]["strategies"]["balanced"]["opportunity_weights"]["demand"] = 0.99
        result = export_scoring_calibration(
            payload,
            output_directory=tmp_dir,
            saved_at=datetime(2026, 7, 15, 18, 30, 1, 123000).astimezone(),
        )
        path = Path(result["path"])
        document = json.loads(path.read_text(encoding="utf-8"))

        assert path.parent == Path(tmp_dir).resolve()
        assert path.name == "评分V2校准标签_balanced_2026-07-15_183001_123.json"
        assert list(document) == [
            "schema_version",
            "export_type",
            "dataset_purpose",
            "exported_at",
            "selection",
            "samples",
            "model_spec",
            "calibration_spec",
            "local_export",
        ]
        assert document["schema_version"] == EXPORT_SCHEMA_VERSION
        assert document["dataset_purpose"] == "model_calibration_labels"
        assert document["samples"][0]["asin"] == "B000000001"
        assert document["samples"][0]["human_label"]["note"] == "结论与证据一致"
        assert document["samples"][0]["sample_seed"] == "seed-original-batch"
        assert document["selection"]["excluded_asins"] == ["B000000099"]
        assert document["samples"][0]["component_scores"]["opportunity"]["demand"]["score"] == 74.0
        assert document["model_spec"]["strategies"]["balanced"]["opportunity_weights"]["demand"] == 0.35
        assert "reason" not in document["samples"][0]["component_scores"]["opportunity"]["demand"]
        assert "page_only_noise" not in document["samples"][0]
        assert "summary" not in document
        assert document["local_export"]["storage"] == "fixed_local_directory"
        assert result["sample_count"] == 1
        assert result["reviewed_count"] == 1
        assert result["writes_database"] is False
        assert not list(Path(tmp_dir).glob("*.tmp"))


def test_calibration_export_rejects_invalid_manual_judgment() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            export_scoring_calibration(
                _calibration_export_payload(judgment="auto_approved"),
                output_directory=tmp_dir,
            )
        except ScoringCalibrationExportError as error:
            assert "人工判断无效" in str(error)
        else:
            raise AssertionError("非法人工判断不应写入校准导出文件")
        assert not list(Path(tmp_dir).iterdir())


def test_reviewed_calibration_export_rejects_pending_samples() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            export_scoring_calibration(
                _calibration_export_payload(judgment="pending", note=""),
                output_directory=tmp_dir,
            )
        except ScoringCalibrationExportError as error:
            assert "不能包含未复核样本" in str(error)
        else:
            raise AssertionError("默认校准标签导出不应包含未复核样本")
        assert not list(Path(tmp_dir).iterdir())


def test_audit_export_allows_pending_samples() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        result = export_scoring_calibration(
            _calibration_export_payload(judgment="pending", note="", export_scope="all"),
            output_directory=tmp_dir,
            saved_at=datetime(2026, 7, 15, 18, 31, 2, 456000).astimezone(),
        )
        path = Path(result["path"])
        document = json.loads(path.read_text(encoding="utf-8"))

        assert path.name == "评分V2审计样本_balanced_2026-07-15_183102_456.json"
        assert document["dataset_purpose"] == "audit_snapshot"
        assert document["samples"][0]["human_label"]["judgment"] == "pending"
        assert result["sample_count"] == 1
        assert result["reviewed_count"] == 0


def test_calibration_review_archive_reads_latest_valid_review() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        first = _calibration_export_payload(judgment="reasonable", note="第一次复核")
        first["exported_at"] = "2026-07-15T10:00:00.000Z"
        export_scoring_calibration(
            first,
            output_directory=tmp_dir,
            saved_at=datetime(2026, 7, 15, 18, 30, 1, 123000).astimezone(),
        )
        latest = _calibration_export_payload(judgment="too_optimistic", note="重新复核")
        latest["exported_at"] = "2026-07-15T11:00:00.000Z"
        export_scoring_calibration(
            latest,
            output_directory=tmp_dir,
            saved_at=datetime(2026, 7, 15, 18, 31, 2, 456000).astimezone(),
        )
        Path(tmp_dir, "损坏文件.json").write_text("{invalid", encoding="utf-8")

        archive = load_exported_scoring_calibration_reviews(input_directory=tmp_dir)

        assert archive["total"] == 1
        assert archive["file_count"] == 2
        assert archive["rows"][0]["human_label"]["judgment"] == "too_optimistic"
        assert archive["rows"][0]["human_label"]["note"] == "重新复核"
        assert archive["rows"][0]["archive_source"] == "exported_json"
        assert archive["rows"][0]["selection"]["excluded_asins"] == ["B000000099"]
        assert len(archive["warnings"]) == 1


if __name__ == "__main__":
    tests = [
        test_missing_values_are_neutral_not_fake_zero,
        test_single_snapshot_never_claims_growth,
        test_high_demand_head_product_is_benchmark_not_direct_entry,
        test_trend_strategy_blocks_low_confidence_growth,
        test_rank_trend_only_uses_same_keyword_history,
        test_strategies_change_weights_but_preserve_source_identity,
        test_global_sort_happens_before_page_slice,
        test_shadow_service_has_no_score_writes,
        test_calibration_flags_surface_strategy_and_evidence_risks,
        test_calibration_sampling_is_stable_and_avoids_duplicate_asins,
        test_calibration_sampling_seed_rotates_and_excludes_reviewed_asins,
        test_calibration_export_writes_valid_json_to_fixed_directory,
        test_calibration_export_rejects_invalid_manual_judgment,
        test_reviewed_calibration_export_rejects_pending_samples,
        test_audit_export_allows_pending_samples,
        test_calibration_review_archive_reads_latest_valid_review,
    ]
    for test in tests:
        test()
    print(f"scoring v2 tests passed: {len(tests)}/{len(tests)}")
