from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import repositories.system as system_repository  # noqa: E402
import repositories.warehouse as warehouse_repository  # noqa: E402
import repositories.products as product_repository  # noqa: E402


def test_system_repository_opens_only_local_application_urls(monkeypatch) -> None:
    opened: list[tuple[str, int]] = []
    monkeypatch.setattr(system_repository.webbrowser, "open", lambda url, new: opened.append((url, new)))
    repository = system_repository.SystemRepository()

    url = repository.open_local_web(
        base_url="http://127.0.0.1:8011/",
        path="#/products",
    )

    assert url == "http://127.0.0.1:8011/#/products"
    assert opened == [(url, 2)]
    with pytest.raises(ValueError, match="当前本地应用"):
        repository.open_local_web(
            base_url="http://127.0.0.1:8011/",
            path="https://example.com",
        )
    with pytest.raises(ValueError, match="本地应用"):
        repository.open_local_web(
            base_url="https://example.com/",
            path="/",
        )


def test_system_repository_exposes_preflight_and_safe_backup(monkeypatch) -> None:
    monkeypatch.setattr(
        system_repository,
        "get_deployment_preflight",
        lambda: {"state": "ready"},
    )
    monkeypatch.setattr(
        system_repository,
        "create_user_data_backup",
        lambda: SimpleNamespace(to_dict=lambda: {"mysql_included": False}),
    )
    repository = system_repository.SystemRepository()

    assert repository.get_deployment_preflight()["state"] == "ready"
    assert repository.backup_local_data()["mysql_included"] is False


def test_warehouse_repository_maps_existing_service_summary(monkeypatch) -> None:
    summary = SimpleNamespace(
        total_rows=7,
        duckdb_path=Path("data/app.duckdb"),
        parquet_dir=Path("data/parquet"),
        manifest_path=Path("data/manifest.json"),
        synced_at="2026-07-16T18:00:00",
        tables=(SimpleNamespace(name="dim_products", rows=7),),
    )
    monkeypatch.setattr(warehouse_repository, "sync_analytics_warehouse", lambda: summary)

    result = warehouse_repository.WarehouseRepository().sync()

    assert result["总行数"] == 7
    assert result["同步表"] == {"dim_products": 7}
    assert result["清单"] == "data\\manifest.json" or result["清单"] == "data/manifest.json"


def test_product_repository_preserves_history_and_review_insight_contract(monkeypatch) -> None:
    history_calls: list[tuple[str, str | None]] = []

    def history(asin, *, score_keyword=None):
        history_calls.append((asin, score_keyword))
        return {"product": {"asin": asin}, "snapshots": [{"snapshot_at": "2026-07-16"}]}

    monkeypatch.setattr(product_repository, "fetch_product_history", history)
    monkeypatch.setattr(
        product_repository,
        "fetch_product_review_insight",
        lambda asin: {"asin": asin, "evidence": {"status": "不可核验"}},
    )

    result = product_repository.ProductRepository().get_history(
        "B0TEST0001", score_keyword="squishy"
    )

    assert history_calls == [("B0TEST0001", "squishy")]
    assert result["review_insight"]["evidence"]["status"] == "不可核验"


def test_product_repository_uses_injected_shared_detail_collector() -> None:
    calls: list[str] = []
    repository = product_repository.ProductRepository(
        detail_collector=lambda asin: calls.append(asin) or {"ASIN": asin}
    )

    result = repository.collect_detail("B0TEST0001")

    assert result == {"ASIN": "B0TEST0001"}
    assert calls == ["B0TEST0001"]
