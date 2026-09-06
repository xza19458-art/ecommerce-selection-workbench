from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api.routers.system as system_router  # noqa: E402
import api.routers.warehouse as warehouse_router  # noqa: E402
import api.routers.products as products_router  # noqa: E402
from api.app import _controller, app  # noqa: E402
from services.product_detail_collection import ProductDetailCollectionError  # noqa: E402


client = TestClient(app)


def test_request_validation_uses_application_error_envelope() -> None:
    response = client.post("/api/tracking/tasks", json={})

    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "request_validation_error"
    assert payload["data"]["errors"]


def test_metric_evidence_route_delegates_project_scope_before_paging(monkeypatch) -> None:
    from services import detail_reparse

    captured: dict = {}

    def fake_list(**kwargs):
        captured.update(kwargs)
        return {
            "summary": {"gap_total": 0},
            "files": [],
            "gaps": {
                "rows": [],
                "total": 0,
                "limit": kwargs["limit"],
                "offset": kwargs["offset"],
                "project_filter": {"active": True, "project_id": kwargs["project_id"]},
            },
        }

    monkeypatch.setattr(detail_reparse, "list_detail_reparse_candidates", fake_list)
    response = client.get(
        "/api/metrics/evidence",
        params={
            "limit": 20,
            "offset": 40,
            "file_limit": 1,
            "stale_days": 45,
            "priority": "planned",
            "project_id": 4,
        },
    )

    assert response.status_code == 200
    assert captured == {
        "limit": 20,
        "offset": 40,
        "file_limit": 1,
        "stale_days": 45,
        "force_refresh": False,
        "priority": "planned",
        "project_id": 4,
    }
    assert response.json()["data"]["gaps"]["project_filter"]["project_id"] == 4


def test_recommendation_route_delegates_all_catalog_filters(monkeypatch) -> None:
    captured: dict = {}

    def fake_page(**kwargs):
        captured.update(kwargs)
        return {
            "rows": [],
            "total": 0,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "sort_by": kwargs["sort_by"],
            "sort_dir": kwargs["sort_dir"],
        }

    monkeypatch.setattr(_controller, "get_recommendations_page", fake_page)
    response = client.get(
        "/api/recommendations",
        params={
            "limit": 20,
            "offset": 20,
            "keyword": "squishy",
            "min_score": 70,
            "max_score": 95,
            "min_price": 10,
            "max_price": 30,
            "min_rating": 4.1,
            "max_rating": 4.9,
            "min_reviews": 20,
            "max_reviews": 500,
            "min_bought": 100,
            "max_bought": 5000,
            "min_rank": 2,
            "max_rank": 40,
            "deal_status": "deal",
            "size_status": "known",
            "sort_by": "price",
            "sort_dir": "asc",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "limit": 20,
        "offset": 20,
        "sort_by": "price",
        "sort_dir": "asc",
        "keyword": "squishy",
        "min_score": 70.0,
        "max_score": 95.0,
        "min_price": 10.0,
        "max_price": 30.0,
        "min_rating": 4.1,
        "max_rating": 4.9,
        "min_reviews": 20,
        "max_reviews": 500,
        "min_bought": 100,
        "max_bought": 5000,
        "min_rank": 2,
        "max_rank": 40,
        "deal_status": "deal",
        "size_status": "known",
    }


def test_service_value_error_is_not_reported_as_server_failure() -> None:
    response = client.post(
        "/api/desktop/open-amazon-product",
        json={"asin": "bad", "marketplace": "US"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "invalid_request"
    assert "ASIN" in payload["message"]


def test_warehouse_status_uses_standard_envelope(monkeypatch) -> None:
    class FakeRepository:
        def get_status(self):
            return {
                "status": "current",
                "message": "分析仓库已同步",
                "last_synced_at": "2026-07-16 18:00:00",
                "manifest_path": "data/manifest.json",
                "duckdb_path": "data/app.duckdb",
                "parquet_dir": "data/parquet",
                "stale_tables": [],
                "missing_tables": [],
                "tables": [],
            }

    monkeypatch.setattr(warehouse_router, "_repository", FakeRepository())
    response = client.get("/api/warehouse/status")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "current"


def test_readiness_uses_503_application_envelope(monkeypatch) -> None:
    class FakeRepository:
        def get_readiness(self):
            return {
                "ready": False,
                "state": "migration_pending",
                "message": "存在待执行迁移",
                "pending": ["20260101_example"],
            }

    monkeypatch.setattr(system_router, "_repository", FakeRepository())

    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "migration_pending"
    assert response.json()["data"]["pending"] == ["20260101_example"]


def test_settings_routes_preserve_read_and_update_contracts(monkeypatch) -> None:
    class FakeRepository:
        def get_settings(self):
            return {
                "settings": {"ui": {"theme": "dark"}},
                "changes": [],
                "schema": {"title": "设置"},
                "defaults": {"ui": {"theme": "system"}},
            }

        def update_settings(self, patch):
            assert patch == {"ui": {"theme": "light"}}
            return {"settings": patch, "changes": []}

    monkeypatch.setattr(system_router, "_repository", FakeRepository())

    read_response = client.get("/api/settings")
    update_response = client.post("/api/settings", json={"patch": {"ui": {"theme": "light"}}})

    assert set(read_response.json()["data"]) == {"settings", "changes", "schema", "defaults"}
    assert set(update_response.json()["data"]) == {"settings", "changes"}


def test_deployment_preflight_and_backup_require_explicit_confirmation(monkeypatch) -> None:
    calls = {"backup": 0}

    class FakeRepository:
        def get_deployment_preflight(self):
            return {
                "checked_at": "2026-07-24T17:00:00+08:00",
                "state": "warning",
                "message": "分析可用，采集待准备",
                "ready_for_analysis": True,
                "ready_for_collection": False,
                "mysql_in_local_backup": False,
                "browser_session_in_local_backup": False,
                "checks": [],
            }

        def backup_local_data(self):
            calls["backup"] += 1
            return {
                "archive_path": "backup.zip",
                "archive_sha256": "A" * 64,
                "file_count": 3,
                "total_bytes": 100,
                "includes_private_config": False,
                "mysql_included": False,
                "message": "备份完成",
            }

    monkeypatch.setattr(system_router, "_repository", FakeRepository())

    preflight = client.get("/api/system/deployment-preflight")
    rejected = client.post("/api/system/local-data-backup", json={"confirmed": False})
    accepted = client.post("/api/system/local-data-backup", json={"confirmed": True})

    assert preflight.status_code == 200
    assert preflight.json()["data"]["ready_for_collection"] is False
    assert rejected.status_code == 400
    assert calls["backup"] == 1
    assert accepted.json()["data"]["mysql_included"] is False


def test_warehouse_sync_preserves_chinese_summary_fields(monkeypatch) -> None:
    class FakeRepository:
        def sync(self):
            return {
                "总行数": 12,
                "DuckDB": "data/app.duckdb",
                "Parquet": "data/parquet",
                "清单": "data/manifest.json",
                "同步时间": "2026-07-16T18:00:00",
                "同步表": {"dim_products": 12},
            }

    monkeypatch.setattr(warehouse_router, "_repository", FakeRepository())
    response = client.post("/api/warehouse/sync")

    assert response.status_code == 200
    assert set(response.json()["data"]) == {"总行数", "DuckDB", "Parquet", "清单", "同步时间", "同步表"}


def test_system_and_warehouse_routes_are_registered_once() -> None:
    expected = {
        ("/api/health", "GET"),
        ("/api/ready", "GET"),
        ("/api/settings", "GET"),
        ("/api/settings", "POST"),
        ("/api/system/deployment-preflight", "GET"),
        ("/api/system/local-data-backup", "POST"),
        ("/api/warehouse/status", "GET"),
        ("/api/warehouse/sync", "POST"),
    }
    paths = app.openapi()["paths"]
    actual = {
        (path, method.upper())
        for path, operations in paths.items()
        for method in operations
        if (path, method.upper()) in expected
    }

    assert actual == expected


def test_product_routes_preserve_filters_context_and_explicit_collection(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeRepository:
        def get_pool_page(self, **kwargs):
            calls.append(("pool", kwargs))
            return {
                "rows": [{"asin": "B0TEST0001", "title": "Example"}],
                "total": 1,
                "limit": kwargs["limit"],
                "offset": kwargs["offset"],
                "sort_by": kwargs["sort_by"],
                "sort_dir": kwargs["sort_dir"],
            }

        def get_history(self, asin, *, score_keyword=None):
            calls.append(("history", (asin, score_keyword)))
            return {
                "product": {"asin": asin, "score_keyword": score_keyword},
                "snapshots": [],
                "rank_context": {"keyword": score_keyword},
            }

        def get_trend(self, asin, *, score_keyword=None):
            calls.append(("trend", (asin, score_keyword)))
            return {
                "sample_size": 1,
                "span_days": 0.0,
                "confidence": "无法判断",
                "confidence_score": 0.0,
                "growth_score": 50.0,
                "metrics": [],
                "promo_warning": None,
                "summary": "样本不足",
            }

        def get_advice(self, asin, *, score_keyword=None):
            calls.append(("advice", (asin, score_keyword)))
            return {"conclusion": "观察", "risk": "未知", "entry_strategy": "补充证据"}

        def collect_detail(self, asin):
            calls.append(("collect", asin))
            return {"ASIN": asin, "状态": "测试委托完成"}

        def get_image(self, asin, *, large=False):
            calls.append(("image", (asin, large)))
            return None

    monkeypatch.setattr(products_router, "_repository", FakeRepository())

    pool = client.get(
        "/api/products",
        params={
            "limit": 20,
            "offset": 40,
            "keyword": "squishy",
            "keyword_exact": "true",
            "keyword_scope": "observed",
            "min_score": 70,
            "max_score": 95,
            "min_price": 10,
            "max_price": 30,
            "min_rating": 4.1,
            "max_rating": 4.9,
            "min_reviews": 20,
            "max_reviews": 500,
            "min_bought": 100,
            "max_bought": 5000,
            "min_rank": 2,
            "max_rank": 40,
            "deal_status": "deal",
            "size_status": "known",
            "sort_by": "price",
            "sort_dir": "asc",
        },
    )
    detail = client.get("/api/products/B0TEST0001", params={"score_keyword": "squishy"})
    trend = client.get("/api/products/B0TEST0001/trend", params={"score_keyword": "squishy"})
    advice = client.get("/api/products/B0TEST0001/advice", params={"score_keyword": "squishy"})
    collected = client.post("/api/products/B0TEST0001/collect-detail")
    image = client.get("/api/products/B0TEST0001/image", params={"large": "true"})

    assert pool.status_code == 200
    pool_call = dict(calls[0][1])
    assert pool_call == {
        "limit": 20,
        "offset": 40,
        "keyword": "squishy",
        "keyword_exact": True,
        "keyword_scope": "observed",
        "min_score": 70.0,
        "max_score": 95.0,
        "min_price": 10.0,
        "max_price": 30.0,
        "min_rating": 4.1,
        "max_rating": 4.9,
        "min_reviews": 20,
        "max_reviews": 500,
        "min_bought": 100,
        "max_bought": 5000,
        "min_rank": 2,
        "max_rank": 40,
        "deal_status": "deal",
        "size_status": "known",
        "sort_by": "price",
        "sort_dir": "asc",
    }
    assert detail.json()["data"]["rank_context"]["keyword"] == "squishy"
    assert trend.json()["data"]["sample_size"] == 1
    assert advice.json()["data"]["entry_strategy"] == "补充证据"
    assert collected.json()["data"]["状态"] == "测试委托完成"
    assert image.status_code == 404
    assert ("collect", "B0TEST0001") in calls
    assert ("image", ("B0TEST0001", True)) in calls


def test_product_routes_are_registered_once_and_removed_from_monolith() -> None:
    expected = {
        ("/api/products", "GET"),
        ("/api/products/{asin}", "GET"),
        ("/api/products/{asin}/collect-detail", "POST"),
        ("/api/products/{asin}/image", "GET"),
        ("/api/products/{asin}/trend", "GET"),
        ("/api/products/{asin}/advice", "GET"),
    }
    paths = app.openapi()["paths"]
    actual = {
        (path, method.upper())
        for path, operations in paths.items()
        for method in operations
        if (path, method.upper()) in expected
    }

    assert actual == expected
    assert '"/api/products' not in (ROOT / "api" / "app.py").read_text(encoding="utf-8")


def test_product_collection_repository_uses_shared_app_controller() -> None:
    collector = products_router._repository._detail_collector

    assert getattr(collector, "__self__", None) is _controller


def test_keyword_opportunity_route_preserves_query_contract(monkeypatch) -> None:
    calls: list[dict] = []

    def page(**kwargs):
        calls.append(kwargs)
        return {
            "rows": [{"keyword": "squishy", "opportunity_score": 76.0}],
            "total": 1,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "sort_by": kwargs["sort_by"],
            "sort_dir": kwargs["sort_dir"],
        }

    monkeypatch.setattr(_controller, "get_keyword_opportunities_page", page)

    response = client.get(
        "/api/keywords/opportunities",
        params={
            "limit": 25,
            "offset": 50,
            "keyword": "squishy",
            "min_products": 3,
            "sort_by": "avg_review_count",
            "sort_dir": "asc",
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "limit": 25,
            "offset": 50,
            "keyword": "squishy",
            "min_products": 3,
            "sort_by": "avg_review_count",
            "sort_dir": "asc",
        }
    ]
    assert response.json()["data"]["rows"][0]["opportunity_score"] == 76.0


def test_product_detail_collection_failure_uses_domain_error_contract(monkeypatch) -> None:
    class FailingRepository:
        def collect_detail(self, _asin):
            raise ProductDetailCollectionError("详情页触发验证码，未写库")

    monkeypatch.setattr(products_router, "_repository", FailingRepository())

    response = client.post("/api/products/B0TEST0001/collect-detail")

    assert response.status_code == 409
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "product_detail_unavailable"
    assert payload["message"] == "详情页触发验证码，未写库"


def test_echarts_runtime_is_served_locally() -> None:
    response = client.get("/vendor/echarts-5.6.0.min.js")

    assert response.status_code == 200
    assert len(response.content) > 1_000_000
    assert b'version="5.6.0"' in response.content
    assert response.headers["cache-control"] == "no-store"
