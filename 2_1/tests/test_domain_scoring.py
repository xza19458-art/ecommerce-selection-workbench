from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import app  # noqa: E402
from api.routers import domain_models as domain_routes  # noqa: E402
from services.domain_scoring import (  # noqa: E402
    DomainScoringError,
    _category_applicability,
    _difference_drivers,
    _profile_parameter_comparison,
    _profile_recommendation,
    _specialized_score,
    build_default_profile_config,
    evaluate_domain_profile_batch,
    evaluate_product_with_domain_profile,
    get_domain_scoring_catalog,
    validate_profile_config,
)
from services.scoring_v2 import StrategyProfile, score_shadow_context  # noqa: E402


AS_OF = datetime(2026, 8, 4, 12, 0, 0)


def _context() -> dict:
    return {
        "product_id": 1,
        "marketplace": "US",
        "asin": "B000000001",
        "title": "Example Product",
        "keyword_id": 7,
        "keyword": "example keyword",
        "rank_snapshot_id": 70,
        "product_snapshot_id": 80,
        "snapshot_at": "2026-08-04 10:00:00",
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


def _history() -> tuple[list[dict], list[dict]]:
    products = [
        {
            "snapshot_id": 80,
            "snapshot_at": "2026-07-21 10:00:00",
            "price": 22.0,
            "rating": 4.5,
            "review_count": 100,
            "monthly_bought": 700,
            "is_deal": False,
        },
        {
            "snapshot_id": 81,
            "snapshot_at": "2026-08-04 10:00:00",
            "price": 22.0,
            "rating": 4.6,
            "review_count": 120,
            "monthly_bought": 1000,
            "is_deal": False,
        },
    ]
    ranks = [
        {"rank_snapshot_id": 70, "snapshot_at": "2026-07-21 10:00:00", "organic_rank": 20, "is_sponsored": False},
        {"rank_snapshot_id": 71, "snapshot_at": "2026-08-04 10:00:00", "organic_rank": 12, "is_sponsored": False},
    ]
    return products, ranks


def test_default_profile_config_is_whitelisted_and_normalized() -> None:
    config = validate_profile_config(build_default_profile_config("balanced"))
    assert sum(config["opportunity_weights"].values()) == pytest.approx(1.0)
    assert sum(config["risk_weights"].values()) == pytest.approx(1.0)
    assert sum(config["axis_blend"].values()) == pytest.approx(1.0)
    assert config["normalization_mode"] == "v2_global_signals"
    assert get_domain_scoring_catalog()["parent_model_version"].startswith("selection-score-v2")


def test_profile_config_rejects_bad_sum_unknown_key_and_inverted_threshold() -> None:
    with pytest.raises(DomainScoringError, match="合计必须为 1"):
        validate_profile_config({"axis_blend": {"opportunity": 0.9, "risk_control": 0.9}})
    with pytest.raises(DomainScoringError, match="未登记参数"):
        validate_profile_config({"arbitrary_python": "print('unsafe')"})
    with pytest.raises(DomainScoringError, match="不能低于观察池"):
        validate_profile_config(
            {"recommendation_policy": {"priority_opportunity": 55, "observe_opportunity": 60}}
        )


def test_category_applicability_distinguishes_match_missing_and_mismatch() -> None:
    assert _category_applicability("Toys & Games", ["Toys & Games > Squeeze Toys"])[0] == 100
    assert _category_applicability("玩具", [])[0] == 35
    assert _category_applicability("Toys & Games", ["Sports & Outdoors"])[0] == 10


def test_custom_strategy_changes_specialized_score_without_changing_confidence() -> None:
    products, ranks = _history()
    baseline = score_shadow_context(_context(), products, ranks, as_of=AS_OF)
    custom_profile = StrategyProfile(
        code="domain-1-v1",
        label="领域测试",
        description="test",
        opportunity_weights={
            "demand": 0.10,
            "growth": 0.60,
            "acceptance": 0.10,
            "visibility": 0.10,
            "improvement_space": 0.10,
        },
        risk_weights={
            "review_barrier": 0.10,
            "incumbent_pressure": 0.10,
            "quality_risk": 0.10,
            "price_risk": 0.10,
            "promo_risk": 0.60,
        },
    )
    custom = score_shadow_context(
        _context(),
        products,
        ranks,
        strategy_profile=custom_profile,
        as_of=AS_OF,
    )
    blend = {"opportunity": 0.6, "risk_control": 0.4}
    assert custom["source_identity"]["strategy"] == "domain-1-v1"
    assert custom["confidence_score"] == baseline["confidence_score"]
    assert _specialized_score(custom, blend) != _specialized_score(baseline, blend)
    assert _difference_drivers(baseline, custom, blend)


def test_profile_recommendation_uses_custom_thresholds_and_trend_guard() -> None:
    config = build_default_profile_config("balanced")
    row = {"opportunity_score": 80, "risk_score": 30, "confidence_score": 80, "trend": {}}
    code, _ = _profile_recommendation(row, config)
    assert code == "priority_validate"
    config["trend_priority_guard"] = True
    code, reason = _profile_recommendation(row, config)
    assert code == "pause"
    assert "趋势保护" in reason


def test_product_evaluation_keeps_baseline_and_custom_results_separate(monkeypatch) -> None:
    import services.domain_scoring as domain_scoring

    config = build_default_profile_config("balanced")
    config["opportunity_weights"] = {
        "demand": 0.10,
        "growth": 0.60,
        "acceptance": 0.10,
        "visibility": 0.10,
        "improvement_space": 0.10,
    }
    profile = {
        "id": 8,
        "marketplace": "US",
        "name": "Toys & Games",
        "description": "玩具领域测试",
        "status": "active",
        "current_version": {"id": 18},
    }
    version = {
        "id": 18,
        "profile_id": 8,
        "version_no": 2,
        "config": config,
        "config_sha256": "a" * 64,
        "scope_type": "category",
        "scope_label": "Amazon 类目",
        "category_scope": "Toys & Games",
        "niche_id": None,
        "niche_name": None,
    }
    products, ranks = _history()
    monkeypatch.setattr(
        domain_scoring,
        "get_domain_profile",
        lambda *_args, **_kwargs: {"profile": profile, "versions": [version]},
    )
    monkeypatch.setattr(
        domain_scoring,
        "load_product_scoring_evidence",
        lambda *_args, **_kwargs: {
            "context": _context(),
            "product_history": products,
            "keyword_rank_history": ranks,
            "available_contexts": [{"keyword_id": 7, "keyword": "example keyword"}],
            "selection_warning": None,
        },
    )
    monkeypatch.setattr(
        domain_scoring,
        "_profile_applicability",
        lambda *_args, **_kwargs: {"score": 100.0, "level": "高", "message": "类目匹配"},
    )
    result = evaluate_product_with_domain_profile(
        8,
        "B000000001",
        keyword="example keyword",
        client=object(),
        as_of=AS_OF,
    )
    assert result["writes_production_score"] is False
    assert result["baseline"]["source_identity"]["strategy"] == "balanced"
    assert result["custom"]["source_identity"]["strategy"] == "domain-8-v2"
    assert result["comparison"]["drivers"]
    assert result["decision"]["code"] != "scope_review"


def test_domain_model_api_contract_uses_structured_response(monkeypatch) -> None:
    monkeypatch.setattr(
        domain_routes,
        "fetch_domain_profiles_page",
        lambda **_kwargs: {"rows": [], "total": 0, "writes_production_score": False},
    )
    client = TestClient(app)
    response = client.get("/api/domain-models?marketplace=US&status=active")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {"rows": [], "total": 0, "writes_production_score": False},
        "message": "",
    }


def test_domain_batch_validation_filters_and_sorts_without_writes(monkeypatch) -> None:
    import services.domain_scoring as domain_scoring

    profile = {
        "id": 8,
        "marketplace": "US",
        "name": "Toys",
        "current_version_id": 18,
        "current_version": {"id": 18},
    }
    version = {
        "id": 18,
        "version_no": 1,
        "scope_type": "category",
        "category_scope": "Toys",
        "niche_id": None,
        "niche_name": None,
        "config": build_default_profile_config("balanced"),
    }
    candidates = [
        {
            "product_id": 1,
            "asin": "B000000001",
            "title": "Changed toy",
            "title_zh": None,
            "product_url": "https://example.test/1",
            "image_url": "",
            "observed_categories": ["Toys & Games"],
            "niche_product_member": False,
            "niche_keyword_ids": [],
            "preferred_keyword": None,
            "scope_evidence": ["类目：Toys & Games"],
        },
        {
            "product_id": 2,
            "asin": "B000000002",
            "title": "Low confidence toy",
            "title_zh": None,
            "product_url": "https://example.test/2",
            "image_url": "",
            "observed_categories": ["Toys & Games"],
            "niche_product_member": False,
            "niche_keyword_ids": [],
            "preferred_keyword": None,
            "scope_evidence": ["类目：Toys & Games"],
        },
    ]
    evidence = {
        "B000000001": {
            "context": {**_context(), "asin": "B000000001", "context_scope": "product_keyword"},
            "selection_warning": None,
        },
        "B000000002": {
            "context": {
                **_context(),
                "product_id": 2,
                "asin": "B000000002",
                "keyword_id": None,
                "keyword": None,
                "context_scope": "product_only",
            },
            "selection_warning": "商品级降置信试算",
        },
    }

    monkeypatch.setattr(domain_scoring, "_resolve_profile_version", lambda *_args: (profile, version))
    monkeypatch.setattr(
        domain_scoring,
        "_load_domain_validation_candidates",
        lambda *_args, **_kwargs: {
            "scope_candidate_total": 2,
            "search_candidate_total": 2,
            "sample": candidates,
        },
    )
    monkeypatch.setattr(domain_scoring, "load_product_scoring_evidence_batch", lambda *_args, **_kwargs: evidence)

    def fake_evaluate(_profile, _version, selected, _db, **kwargs):
        asin = selected["context"]["asin"]
        changed = asin.endswith("1")
        confidence = 70.0 if changed else 20.0
        applicability = dict(kwargs["applicability"])
        applicability.update({"minimum_required": 60.0, "eligible": True})
        return {
            "context": {
                "asin": asin,
                "keyword_id": selected["context"].get("keyword_id"),
                "keyword": selected["context"].get("keyword"),
                "context_scope": selected["context"]["context_scope"],
                "snapshot_at": "2026-08-04 10:00:00",
            },
            "baseline": {"specialized_score": 60.0},
            "custom": {
                "opportunity_score": 72.0,
                "risk_score": 40.0,
                "confidence_score": confidence,
                "confidence_level": "高" if changed else "低",
                "confidence_components": [],
            },
            "comparison": {
                "baseline_specialized_score": 60.0,
                "custom_specialized_score": 68.0 if changed else 60.0,
                "specialized_score_delta": 8.0 if changed else 0.0,
                "drivers": [],
            },
            "applicability": applicability,
            "decision": {"code": "observe", "label": "可进入观察池", "reason": "test"},
            "evaluation_fingerprint": asin.lower(),
        }

    monkeypatch.setattr(domain_scoring, "_evaluate_loaded_domain_evidence", fake_evaluate)
    result = evaluate_domain_profile_batch(8, client=object(), as_of=AS_OF)
    assert [row["asin"] for row in result["rows"]] == ["B000000001", "B000000002"]
    assert result["candidate_summary"]["changed_total"] == 1
    assert result["candidate_summary"]["context_counts"] == {"product_keyword": 1, "product_only": 1}
    assert result["writes_production_score"] is False

    low_confidence = evaluate_domain_profile_batch(
        8,
        signal="low_confidence",
        client=object(),
        as_of=AS_OF,
    )
    assert [row["asin"] for row in low_confidence["rows"]] == ["B000000002"]


def test_profile_parameter_comparison_exposes_unchanged_parent_strategy() -> None:
    config = build_default_profile_config("balanced")
    assert _profile_parameter_comparison(config)["identical_to_parent"] is True
    config["opportunity_weights"] = {
        "demand": 0.30,
        "growth": 0.20,
        "acceptance": 0.15,
        "visibility": 0.20,
        "improvement_space": 0.15,
    }
    comparison = _profile_parameter_comparison(config)
    assert comparison["identical_to_parent"] is False
    assert comparison["changed_parameter_count"] >= 2


def test_domain_batch_api_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        domain_routes,
        "evaluate_domain_profile_batch",
        lambda *_args, **_kwargs: {"rows": [], "total": 0, "writes_production_score": False},
    )
    client = TestClient(app)
    response = client.post(
        "/api/domain-models/8/evaluate-batch",
        json={"version_id": 18, "sample_limit": 20, "limit": 10},
    )
    assert response.status_code == 200
    assert response.json()["data"]["writes_production_score"] is False


def test_domain_profile_migration_has_version_and_deduplication_contract() -> None:
    sql = (ROOT / "database" / "migrations" / "20260804_domain_scoring_profiles_v1.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS scoring_profiles" in sql
    assert "CREATE TABLE IF NOT EXISTS scoring_profile_versions" in sql
    assert "UNIQUE KEY uk_scoring_profile_market_name" in sql
    assert "UNIQUE KEY uk_scoring_profile_version_no" in sql
    assert "UNIQUE KEY uk_scoring_profile_config_hash" in sql
    assert "fk_scoring_profile_current_version" in sql
