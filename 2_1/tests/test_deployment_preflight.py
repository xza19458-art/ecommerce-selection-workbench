from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.browser_runtime import BrowserRuntimeError  # noqa: E402
from services.deployment_preflight import get_deployment_preflight  # noqa: E402


def _database_config(path: Path, *, password: str = "secret-value") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "host": "localhost",
                "port": 3306,
                "user": "root",
                "password": password,
                "database": "amazon_selection",
                "charset": "utf8mb4",
            }
        ),
        encoding="utf-8",
    )


def _ready_database(client):
    return {
        "ready": True,
        "state": "ready",
        "message": "数据库连接、结构与迁移台账均已就绪",
        "database": client.config.database,
        "mysql_version": "8.0.42",
        "business_table_count": 28,
        "pending": [],
        "failed": [],
    }


def _ready_browser():
    return {
        "status": "ready",
        "message": "浏览器已就绪",
        "browser": {"name": "Google Chrome", "version": "150.0.1", "path": "chrome.exe"},
        "driver": {"version": "150.0.1", "path": "driver.exe", "source": "cache"},
    }


def test_preflight_reports_ready_without_exposing_password(tmp_path: Path) -> None:
    root = tmp_path / "user"
    root.mkdir()
    _database_config(root / "config" / "database.json")

    report = get_deployment_preflight(
        root=root,
        database_config_path=root / "config" / "database.json",
        readiness_loader=_ready_database,
        browser_loader=_ready_browser,
        warehouse_loader=lambda **_kwargs: {
            "status": "current",
            "message": "分析仓库已同步",
            "last_synced_at": "2026-07-24 17:00:00",
            "stale_tables": [],
            "missing_tables": [],
        },
    )

    assert report["state"] == "ready"
    assert report["ready_for_analysis"] is True
    assert report["ready_for_collection"] is True
    assert "secret-value" not in json.dumps(report, ensure_ascii=False)
    database_details = next(
        item["details"] for item in report["checks"] if item["id"] == "database_config"
    )
    assert "password" not in database_details


def test_missing_database_config_blocks_analysis_but_still_checks_browser(
    tmp_path: Path,
) -> None:
    root = tmp_path / "user"
    root.mkdir()
    calls = {"browser": 0, "warehouse": 0}

    def browser():
        calls["browser"] += 1
        return _ready_browser()

    def warehouse(**_kwargs):
        calls["warehouse"] += 1
        return {}

    report = get_deployment_preflight(
        root=root,
        database_config_path=root / "config" / "database.json",
        browser_loader=browser,
        warehouse_loader=warehouse,
    )

    assert report["state"] == "blocked"
    assert report["ready_for_analysis"] is False
    assert calls == {"browser": 1, "warehouse": 0}


def test_browser_failure_only_blocks_collection_and_preserves_safe_details(
    tmp_path: Path,
) -> None:
    root = tmp_path / "user"
    root.mkdir()
    _database_config(root / "config" / "database.json")

    def browser():
        raise BrowserRuntimeError(
            "已找到 Chrome，但缺少匹配驱动。",
            code="chrome_driver_required",
            details={
                "can_download_driver": True,
                "expected_driver": "150.0.1.x",
                "detected_driver_versions": ["149.0.0.0"],
                "cookie": "must-not-leak",
            },
        )

    report = get_deployment_preflight(
        root=root,
        database_config_path=root / "config" / "database.json",
        readiness_loader=_ready_database,
        browser_loader=browser,
        warehouse_loader=lambda **_kwargs: {
            "status": "stale",
            "message": "分析仓库需要同步",
            "stale_tables": ["dim_products"],
            "missing_tables": [],
        },
    )

    assert report["state"] == "warning"
    assert report["ready_for_analysis"] is True
    assert report["ready_for_collection"] is False
    browser_check = next(
        item for item in report["checks"] if item["id"] == "browser_runtime"
    )
    assert browser_check["details"]["can_download_driver"] is True
    assert browser_check["details"]["expected_driver"] == "150.0.1.x"
    assert "cookie" not in browser_check["details"]


def test_inventory_counts_only_migration_allowlist(tmp_path: Path) -> None:
    root = tmp_path / "user"
    _database_config(root / "config" / "database.json")
    (root / "html").mkdir()
    (root / "html" / "page.html").write_text("html", encoding="utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "desktop.log").write_text("log", encoding="utf-8")

    report = get_deployment_preflight(
        root=root,
        database_config_path=root / "config" / "database.json",
        readiness_loader=_ready_database,
        browser_loader=_ready_browser,
        warehouse_loader=lambda **_kwargs: {
            "status": "current",
            "message": "current",
            "stale_tables": [],
            "missing_tables": [],
        },
    )
    inventory = next(
        item["details"]
        for item in report["checks"]
        if item["id"] == "local_data_inventory"
    )

    assert inventory["file_count"] == 1
    assert inventory["private_config_count"] == 1
    assert inventory["excluded_area_count"] == 1
