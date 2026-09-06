"""Read-only first-deployment and local runtime diagnostics."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Callable

from core.browser_runtime import BrowserRuntimeError, get_browser_runtime_status
from database.migration_runner import get_database_readiness
from database.mysql_client import DatabaseConfig, MySQLClient
from pkg_paths import is_frozen, user_data_root
from services.analytics_warehouse import get_warehouse_status
from services.user_data_transfer import (
    ALWAYS_EXCLUDED,
    DEFAULT_DATA_DIRECTORIES,
    PRIVATE_CONFIG_FILES,
    SAFE_CONFIG_FILES,
)


CheckLoader = Callable[..., dict[str, Any]]


def get_deployment_preflight(
    *,
    root: str | Path | None = None,
    frozen: bool | None = None,
    database_config_path: str | Path | None = None,
    readiness_loader: CheckLoader = get_database_readiness,
    browser_loader: CheckLoader = get_browser_runtime_status,
    warehouse_loader: CheckLoader = get_warehouse_status,
) -> dict[str, Any]:
    """Return a stable report without changing config, schema, drivers, or data."""

    data_root = Path(root or user_data_root()).resolve()
    config_path = Path(
        database_config_path or data_root / "config" / "database.json"
    ).resolve()
    frozen_mode = is_frozen() if frozen is None else bool(frozen)
    checks: list[dict[str, Any]] = []

    root_ready, root_message = _check_data_root(data_root)
    checks.append(
        _check(
            "user_data_root",
            "用户数据目录",
            "ready" if root_ready else "blocked",
            root_message,
            {
                "path": str(data_root),
                "runtime_mode": "desktop_package" if frozen_mode else "source",
            },
        )
    )

    database_client: MySQLClient | None = None
    database_config: DatabaseConfig | None = None
    database_password = ""
    try:
        _validate_database_config_file(config_path)
        database_config = DatabaseConfig.from_file(config_path)
        database_password = database_config.password
        checks.append(
            _check(
                "database_config",
                "数据库配置",
                "ready",
                "数据库配置格式完整；密码内容未在预检结果中显示。",
                {
                    "path": str(config_path),
                    "host": database_config.host,
                    "port": database_config.port,
                    "database": database_config.database,
                    "charset": database_config.charset,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001 - convert config/vendor failures to stable output.
        checks.append(
            _check(
                "database_config",
                "数据库配置",
                "blocked",
                _redact(f"数据库配置不可用：{exc}", database_password),
                {"path": str(config_path)},
            )
        )

    database_ready = False
    database_report: dict[str, Any] = {}
    if database_config is not None:
        try:
            database_client = MySQLClient(database_config)
            database_report = readiness_loader(database_client)
        except Exception as exc:  # noqa: BLE001 - injected/read-only diagnostics must stay stable.
            database_report = {
                "ready": False,
                "state": "database_unavailable",
                "message": f"数据库只读检查失败：{exc}",
            }
        database_ready = bool(database_report.get("ready"))
        checks.append(
            _check(
                "mysql_readiness",
                "MySQL 就绪状态",
                "ready" if database_ready else "blocked",
                _redact(
                    str(database_report.get("message") or "数据库未返回就绪结论。"),
                    database_password,
                ),
                {
                    "state": str(database_report.get("state") or "unknown"),
                    "database": database_report.get("database"),
                    "mysql_version": database_report.get("mysql_version"),
                    "business_table_count": int(
                        database_report.get("business_table_count") or 0
                    ),
                    "pending_count": len(database_report.get("pending") or []),
                    "failed_count": len(database_report.get("failed") or []),
                },
            )
        )
    else:
        checks.append(
            _check(
                "mysql_readiness",
                "MySQL 就绪状态",
                "blocked",
                "数据库配置尚未就绪，因此未连接 MySQL。",
                {"state": "config_unavailable"},
            )
        )

    browser_ready = False
    try:
        browser_report = browser_loader()
        browser_ready = browser_report.get("status") == "ready"
        checks.append(
            _check(
                "browser_runtime",
                "Chrome 与采集驱动",
                "ready" if browser_ready else "blocked",
                str(browser_report.get("message") or "浏览器运行时状态未知。"),
                _safe_browser_details(browser_report),
            )
        )
    except BrowserRuntimeError as exc:
        checks.append(
            _check(
                "browser_runtime",
                "Chrome 与采集驱动",
                "blocked",
                str(exc),
                {
                    "code": exc.code,
                    **_safe_browser_details(exc.details),
                },
            )
        )
    except Exception as exc:  # noqa: BLE001 - vendor detection is normalized for the UI.
        checks.append(
            _check(
                "browser_runtime",
                "Chrome 与采集驱动",
                "blocked",
                f"浏览器运行时检查失败：{exc}",
                {"code": "browser_check_failed"},
            )
        )

    if database_ready and database_client is not None:
        try:
            warehouse_report = warehouse_loader(client=database_client)
            warehouse_state = str(warehouse_report.get("status") or "missing")
            warehouse_status = "ready" if warehouse_state == "current" else "warning"
            checks.append(
                _check(
                    "analytics_warehouse",
                    "本地分析仓库",
                    warehouse_status,
                    str(warehouse_report.get("message") or "分析仓库状态未知。"),
                    {
                        "state": warehouse_state,
                        "last_synced_at": warehouse_report.get("last_synced_at"),
                        "stale_table_count": len(
                            warehouse_report.get("stale_tables") or []
                        ),
                        "missing_table_count": len(
                            warehouse_report.get("missing_tables") or []
                        ),
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 - warehouse is an optional acceleration layer.
            checks.append(
                _check(
                    "analytics_warehouse",
                    "本地分析仓库",
                    "warning",
                    f"分析仓库只读检查失败，应用仍可回退 MySQL：{exc}",
                    {"state": "unavailable"},
                )
            )
    else:
        checks.append(
            _check(
                "analytics_warehouse",
                "本地分析仓库",
                "warning",
                "MySQL 未就绪，暂不判断分析仓库新旧；仓库不会替代主库。",
                {"state": "not_checked"},
            )
        )

    inventory = _local_data_inventory(data_root)
    inventory_status = "ready" if root_ready else "warning"
    checks.append(
        _check(
            "local_data_inventory",
            "本地证据概览",
            inventory_status,
            (
                f"发现 {inventory['file_count']} 个可迁移本地文件，"
                f"共 {inventory['total_bytes']} 字节；MySQL 与浏览器会话不在其中。"
            ),
            inventory,
        )
    )

    ready_for_analysis = root_ready and database_ready
    ready_for_collection = ready_for_analysis and browser_ready
    if not ready_for_analysis:
        state = "blocked"
        message = "部署尚未就绪：请先处理用户目录或 MySQL 阻断项。"
    elif not ready_for_collection:
        state = "warning"
        message = "分析功能可用，但 Chrome/驱动未就绪，联网采集暂不可用。"
    elif any(item["status"] == "warning" for item in checks):
        state = "warning"
        message = "核心分析与采集可用，但存在可回退或待同步提示。"
    else:
        state = "ready"
        message = "核心分析与采集环境均已就绪。"
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "state": state,
        "message": message,
        "ready_for_analysis": ready_for_analysis,
        "ready_for_collection": ready_for_collection,
        "mysql_in_local_backup": False,
        "browser_session_in_local_backup": False,
        "checks": checks,
    }


def _check(
    check_id: str,
    label: str,
    status: str,
    message: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "message": message,
        "details": details,
    }


def _validate_database_config_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"未找到数据库配置文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"数据库配置不是有效 JSON：{path}") from exc
    if not isinstance(data, dict):
        raise ValueError("数据库配置顶层必须是 JSON 对象。")
    missing = [
        key
        for key in ("host", "user", "database")
        if not str(data.get(key) or "").strip()
    ]
    if missing:
        raise ValueError("数据库配置缺少必填项：" + "、".join(missing))


def _check_data_root(root: Path) -> tuple[bool, str]:
    if not root.exists():
        parent = _nearest_existing_parent(root)
        writable = bool(parent and os.access(parent, os.W_OK))
        if writable:
            return True, f"用户数据目录尚未创建，但上级目录可写：{root}"
        return False, f"用户数据目录不存在，且未找到可写上级目录：{root}"
    if not root.is_dir():
        return False, f"用户数据路径不是目录：{root}"
    if not os.access(root, os.R_OK | os.W_OK):
        return False, f"用户数据目录不可读写：{root}"
    return True, f"用户数据目录可读写：{root}"


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() and current.is_dir() else None


def _safe_browser_details(report: dict[str, Any]) -> dict[str, Any]:
    browser = report.get("browser")
    driver = report.get("driver")
    details: dict[str, Any] = {
        "can_download_driver": bool(report.get("can_download_driver")),
    }
    if isinstance(browser, dict):
        details["browser"] = {
            "name": browser.get("name"),
            "version": browser.get("version"),
            "path": browser.get("path"),
        }
    if isinstance(driver, dict):
        details["driver"] = {
            "version": driver.get("version"),
            "path": driver.get("path"),
            "source": driver.get("source"),
        }
    if report.get("expected_driver"):
        details["expected_driver"] = report.get("expected_driver")
    if isinstance(report.get("detected_driver_versions"), list):
        details["detected_driver_versions"] = report["detected_driver_versions"]
    return details


def _local_data_inventory(root: Path) -> dict[str, Any]:
    areas: list[dict[str, Any]] = []
    total_files = 0
    total_bytes = 0
    for relative in (*DEFAULT_DATA_DIRECTORIES, *SAFE_CONFIG_FILES):
        area_path = root.joinpath(*relative.split("/"))
        file_count, byte_count = _path_inventory(area_path)
        total_files += file_count
        total_bytes += byte_count
        areas.append(
            {
                "path": relative,
                "exists": area_path.exists(),
                "file_count": file_count,
                "total_bytes": byte_count,
            }
        )
    private_config_count = sum(
        1 for relative in PRIVATE_CONFIG_FILES if root.joinpath(*relative.split("/")).is_file()
    )
    excluded_area_count = sum(1 for relative in ALWAYS_EXCLUDED if (root / relative).exists())
    return {
        "root": str(root),
        "file_count": total_files,
        "total_bytes": total_bytes,
        "areas": areas,
        "private_config_count": private_config_count,
        "excluded_area_count": excluded_area_count,
        "mysql_included": False,
        "browser_session_included": False,
    }


def _path_inventory(path: Path) -> tuple[int, int]:
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 0, 0
    if not path.is_dir():
        return 0, 0
    count = 0
    total = 0
    for current_root, dir_names, file_names in os.walk(path, followlinks=False):
        current = Path(current_root)
        dir_names[:] = [
            name for name in dir_names if not (current / name).is_symlink()
        ]
        for name in file_names:
            file_path = current / name
            if file_path.is_symlink():
                continue
            try:
                total += file_path.stat().st_size
                count += 1
            except OSError:
                continue
    return count, total


def _redact(message: str, secret: str) -> str:
    text = str(message)
    if secret:
        text = text.replace(secret, "***")
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text
    if isinstance(parsed, dict) and "password" in parsed:
        parsed["password"] = "***"
    return json.dumps(parsed, ensure_ascii=False)
