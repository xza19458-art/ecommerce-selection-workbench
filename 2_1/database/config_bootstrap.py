"""Prepare the writable MySQL config used by frozen desktop builds."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil

from pkg_paths import is_frozen, resource_path, user_data_path


class DatabaseConfigPreparationError(RuntimeError):
    """Raised when a packaged database config cannot be prepared safely."""


@dataclass(frozen=True)
class DatabaseConfigPreparation:
    path: Path
    status: str
    source_path: Path | None = None


def _validate_config_source(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatabaseConfigPreparationError(f"数据库配置文件无法读取或不是有效 JSON：{path}") from exc
    if not isinstance(data, dict) or not all(data.get(key) for key in ("host", "user", "database")):
        raise DatabaseConfigPreparationError(
            f"数据库配置缺少 host、user 或 database：{path}"
        )


def prepare_database_config(
    *,
    frozen: bool | None = None,
    target_path: Path | None = None,
    legacy_path: Path | None = None,
    template_path: Path | None = None,
) -> DatabaseConfigPreparation:
    """Ensure a frozen build has a config outside ``_internal``.

    Early package instructions led users to copy ``database.json`` beside the
    bundled examples under ``_internal/config``.  Preserve that valid file by
    copying it to the supported writable location.  A fresh package receives
    an editable example instead.  Source mode remains non-mutating.
    """

    frozen = is_frozen() if frozen is None else frozen
    target = (target_path or user_data_path("config", "database.json")).resolve()
    if target.is_file():
        return DatabaseConfigPreparation(target, "existing")
    if target.exists():
        raise DatabaseConfigPreparationError(f"数据库配置路径不是文件：{target}")
    if not frozen:
        return DatabaseConfigPreparation(target, "missing")

    legacy = (legacy_path or resource_path("config", "database.json")).resolve()
    template = (template_path or resource_path("config", "database.example.json")).resolve()
    if legacy != target and legacy.is_file():
        source = legacy
        status = "migrated"
    else:
        source = template
        status = "created"
    if not source.is_file():
        raise DatabaseConfigPreparationError(f"未找到数据库配置模板：{source}")

    _validate_config_source(source)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError as exc:
        raise DatabaseConfigPreparationError(f"无法创建数据库配置文件：{target}") from exc
    return DatabaseConfigPreparation(target, status, source)
