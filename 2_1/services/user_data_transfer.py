"""Create and restore verified local evidence archives.

The archive intentionally excludes MySQL data and browser session state.
MySQL remains the source of truth and must be migrated separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Any, Iterable
import uuid
import zipfile

from pkg_paths import user_data_root


ARCHIVE_SCHEMA = "amazon-selection-local-data-v1"
MANIFEST_NAME = "migration_manifest.json"
DATA_PREFIX = "data/"
DEFAULT_DATA_DIRECTORIES = (
    "html",
    "reviews",
    "exports",
    "数据结果",
    "data_warehouse",
)
SAFE_CONFIG_FILES = (
    "config/settings.json",
    "config/crawl_queues.json",
)
PRIVATE_CONFIG_FILES = (
    "config/database.json",
    "config/agent.json",
    "config/translation.json",
    "config/warehouse.json",
)
ALWAYS_EXCLUDED = (
    "webview_state",
    "logs",
    "cache",
    "drivers",
    ".argos",
    "migration_restore_backups",
)


class UserDataTransferError(ValueError):
    """Raised when an archive cannot be created or restored safely."""


@dataclass(frozen=True)
class BackupSummary:
    archive_path: Path
    archive_sha256: str
    file_count: int
    total_bytes: int
    includes_private_config: bool
    mysql_included: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": str(self.archive_path),
            "archive_sha256": self.archive_sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "includes_private_config": self.includes_private_config,
            "mysql_included": self.mysql_included,
            "message": (
                f"本地证据迁移包已生成，共 {self.file_count} 个文件；"
                "MySQL 业务数据未包含，需单独备份。"
            ),
        }


@dataclass(frozen=True)
class RestoreSummary:
    archive_path: Path
    target_root: Path
    written_count: int
    unchanged_count: int
    overwritten_count: int
    backup_dir: Path | None
    includes_private_config: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": str(self.archive_path),
            "target_root": str(self.target_root),
            "written_count": self.written_count,
            "unchanged_count": self.unchanged_count,
            "overwritten_count": self.overwritten_count,
            "backup_dir": str(self.backup_dir) if self.backup_dir else None,
            "includes_private_config": self.includes_private_config,
            "message": (
                f"本地证据恢复完成：写入 {self.written_count}，"
                f"已存在且一致 {self.unchanged_count}，覆盖 {self.overwritten_count}。"
            ),
        }


def default_backup_directory() -> Path:
    return Path.home() / "Documents" / "AmazonSelectionWorkbenchBackups"


def default_backup_path(*, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S")
    return default_backup_directory() / f"Amazon选品助手_本地证据迁移包_{timestamp}.zip"


def create_user_data_backup(
    *,
    source_root: str | Path | None = None,
    output_path: str | Path | None = None,
    include_private_config: bool = False,
    now: datetime | None = None,
) -> BackupSummary:
    root_input = Path(source_root or user_data_root())
    if root_input.exists() and _is_link_or_reparse(root_input):
        raise UserDataTransferError(f"用户数据根目录不能是链接或重解析点：{root_input}")
    root = root_input.resolve()
    destination = Path(output_path or default_backup_path(now=now)).resolve()
    if not root.is_dir():
        raise UserDataTransferError(f"用户数据目录不存在：{root}")
    if _is_link_or_reparse(root):
        raise UserDataTransferError(f"用户数据根目录不能是链接或重解析点：{root}")
    if _is_within(destination, root):
        raise UserDataTransferError("迁移包不能写在用户数据目录内部，避免递归打包。")
    if destination.exists():
        raise UserDataTransferError(f"目标迁移包已存在：{destination}")

    selected = _collect_backup_files(root, include_private_config=include_private_config)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    created_at = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    entries: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(
            temporary,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for relative, source in selected:
                archive_name = DATA_PREFIX + relative.as_posix()
                digest = hashlib.sha256()
                size = 0
                with source.open("rb") as reader, archive.open(archive_name, "w") as writer:
                    while chunk := reader.read(1024 * 1024):
                        writer.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                entries.append(
                    {
                        "path": relative.as_posix(),
                        "size": size,
                        "sha256": digest.hexdigest().upper(),
                    }
                )

            manifest = {
                "schema": ARCHIVE_SCHEMA,
                "created_at": created_at,
                "profile": "private" if include_private_config else "safe",
                "includes_private_config": include_private_config,
                "mysql_included": False,
                "browser_session_included": False,
                "excluded": list(ALWAYS_EXCLUDED),
                "files": entries,
            }
            archive.writestr(
                MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return BackupSummary(
        archive_path=destination,
        archive_sha256=_sha256_file(destination),
        file_count=len(entries),
        total_bytes=sum(int(entry["size"]) for entry in entries),
        includes_private_config=include_private_config,
    )


def inspect_user_data_archive(archive_path: str | Path) -> dict[str, Any]:
    archive_file = Path(archive_path).resolve()
    manifest, entries = _validate_archive(archive_file)
    return {
        "archive_path": str(archive_file),
        "archive_sha256": _sha256_file(archive_file),
        "schema": manifest["schema"],
        "created_at": manifest.get("created_at"),
        "profile": manifest.get("profile"),
        "includes_private_config": bool(manifest.get("includes_private_config")),
        "mysql_included": bool(manifest.get("mysql_included")),
        "file_count": len(entries),
        "total_bytes": sum(int(entry["size"]) for entry in entries),
    }


def restore_user_data_backup(
    archive_path: str | Path,
    *,
    target_root: str | Path | None = None,
    overwrite: bool = False,
    now: datetime | None = None,
) -> RestoreSummary:
    archive_file = Path(archive_path).resolve()
    root_input = Path(target_root or user_data_root())
    if root_input.exists() and _is_link_or_reparse(root_input):
        raise UserDataTransferError(f"用户数据根目录不能是链接或重解析点：{root_input}")
    root = root_input.resolve()
    manifest, entries = _validate_archive(archive_file)
    root.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(root):
        raise UserDataTransferError(f"用户数据根目录不能是链接或重解析点：{root}")

    existing_same: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for entry in entries:
        relative = _validated_relative_path(entry["path"])
        target = _safe_target_path(root, relative)
        if _is_link_or_reparse(target):
            raise UserDataTransferError(f"恢复目标不能是链接或重解析点：{target}")
        if not target.exists():
            missing.append(entry)
        elif not target.is_file():
            raise UserDataTransferError(f"恢复目标已存在且不是普通文件：{target}")
        elif target.stat().st_size == int(entry["size"]) and _sha256_file(target) == entry["sha256"]:
            existing_same.append(entry)
        else:
            conflicts.append(entry)

    if conflicts and not overwrite:
        examples = "、".join(str(entry["path"]) for entry in conflicts[:3])
        raise UserDataTransferError(
            f"发现 {len(conflicts)} 个同名但内容不同的文件，默认不覆盖：{examples}"
        )

    backup_dir: Path | None = None
    if conflicts:
        timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S")
        backup_dir = root / "migration_restore_backups" / timestamp
        if backup_dir.exists():
            raise UserDataTransferError(f"恢复前备份目录已存在：{backup_dir}")

    stage_root = Path(tempfile.mkdtemp(prefix=".migration_restore_", dir=root))
    try:
        with zipfile.ZipFile(archive_file, "r") as archive:
            for entry in missing + conflicts:
                relative = _validated_relative_path(entry["path"])
                staged = _safe_target_path(stage_root, relative)
                staged.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(DATA_PREFIX + relative.as_posix(), "r") as reader, staged.open("wb") as writer:
                    shutil.copyfileobj(reader, writer, length=1024 * 1024)

        if backup_dir:
            for entry in conflicts:
                relative = _validated_relative_path(entry["path"])
                current = _safe_target_path(root, relative)
                if current.is_file():
                    backup_target = _safe_target_path(backup_dir, relative)
                    backup_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(current, backup_target)

        for entry in missing + conflicts:
            relative = _validated_relative_path(entry["path"])
            staged = _safe_target_path(stage_root, relative)
            target = _safe_target_path(root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.restore")
            shutil.copy2(staged, temporary_target)
            os.replace(temporary_target, target)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    return RestoreSummary(
        archive_path=archive_file,
        target_root=root,
        written_count=len(missing) + len(conflicts),
        unchanged_count=len(existing_same),
        overwritten_count=len(conflicts),
        backup_dir=backup_dir,
        includes_private_config=bool(manifest.get("includes_private_config")),
    )


def _collect_backup_files(
    root: Path,
    *,
    include_private_config: bool,
) -> list[tuple[PurePosixPath, Path]]:
    relative_paths: list[str] = list(DEFAULT_DATA_DIRECTORIES) + list(SAFE_CONFIG_FILES)
    if include_private_config:
        relative_paths.extend(PRIVATE_CONFIG_FILES)

    selected: dict[str, Path] = {}
    for relative_value in relative_paths:
        relative = _validated_relative_path(relative_value)
        source = _safe_target_path(root, relative)
        if not source.exists():
            continue
        if _is_link_or_reparse(source):
            raise UserDataTransferError(f"不打包链接或重解析点：{source}")
        if source.is_file():
            selected[relative.as_posix()] = source
            continue
        if not source.is_dir():
            raise UserDataTransferError(f"迁移白名单路径既不是文件也不是目录：{source}")
        for file_path in _walk_regular_files(source):
            file_relative = PurePosixPath(file_path.relative_to(root).as_posix())
            selected[file_relative.as_posix()] = file_path
    return [
        (PurePosixPath(relative), selected[relative])
        for relative in sorted(selected)
    ]


def _walk_regular_files(directory: Path) -> Iterable[Path]:
    for current_root, dir_names, file_names in os.walk(directory, followlinks=False):
        current = Path(current_root)
        for dir_name in list(dir_names):
            child = current / dir_name
            if _is_link_or_reparse(child):
                raise UserDataTransferError(f"不打包链接或重解析点：{child}")
        for file_name in file_names:
            child = current / file_name
            if _is_link_or_reparse(child):
                raise UserDataTransferError(f"不打包链接或重解析点：{child}")
            if not child.is_file():
                raise UserDataTransferError(f"迁移白名单中存在非普通文件：{child}")
            yield child


def _validate_archive(archive_file: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not archive_file.is_file():
        raise UserDataTransferError(f"未找到迁移包：{archive_file}")
    try:
        with zipfile.ZipFile(archive_file, "r") as archive:
            names = [info.filename for info in archive.infolist()]
            if len(names) != len(set(names)):
                raise UserDataTransferError("迁移包包含重复路径。")
            if MANIFEST_NAME not in names:
                raise UserDataTransferError("迁移包缺少清单。")
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema") != ARCHIVE_SCHEMA:
                raise UserDataTransferError("迁移包 schema 不受支持。")
            if manifest.get("mysql_included") is not False:
                raise UserDataTransferError("迁移包错误声明包含 MySQL 数据。")
            entries = manifest.get("files")
            if not isinstance(entries, list):
                raise UserDataTransferError("迁移包文件清单格式错误。")
            expected_names = {MANIFEST_NAME}
            listed_paths: set[str] = set()
            validated_entries: list[dict[str, Any]] = []
            include_private = bool(manifest.get("includes_private_config"))
            for raw in entries:
                if not isinstance(raw, dict):
                    raise UserDataTransferError("迁移包文件项格式错误。")
                relative = _validated_relative_path(raw.get("path"))
                if relative.as_posix() in listed_paths:
                    raise UserDataTransferError(
                        f"迁移包清单包含重复路径：{relative.as_posix()}"
                    )
                listed_paths.add(relative.as_posix())
                if not _is_allowed_archive_path(relative, include_private=include_private):
                    raise UserDataTransferError(f"迁移包包含非白名单路径：{relative.as_posix()}")
                size = _non_negative_int(raw.get("size"), field="size")
                digest = str(raw.get("sha256") or "").upper()
                if len(digest) != 64 or any(char not in "0123456789ABCDEF" for char in digest):
                    raise UserDataTransferError(f"迁移包哈希格式错误：{relative.as_posix()}")
                archive_name = DATA_PREFIX + relative.as_posix()
                expected_names.add(archive_name)
                try:
                    info = archive.getinfo(archive_name)
                except KeyError as exc:
                    raise UserDataTransferError(f"迁移包缺少文件：{relative.as_posix()}") from exc
                if info.is_dir() or info.file_size != size:
                    raise UserDataTransferError(f"迁移包文件大小不一致：{relative.as_posix()}")
                actual_digest = _sha256_zip_entry(archive, archive_name)
                if actual_digest != digest:
                    raise UserDataTransferError(f"迁移包文件哈希不一致：{relative.as_posix()}")
                validated_entries.append(
                    {"path": relative.as_posix(), "size": size, "sha256": digest}
                )
            if set(names) != expected_names:
                extras = sorted(set(names) - expected_names)
                raise UserDataTransferError(
                    "迁移包包含清单外文件：" + "、".join(extras[:3])
                )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        if isinstance(exc, UserDataTransferError):
            raise
        raise UserDataTransferError(f"迁移包无法读取：{archive_file}") from exc
    return manifest, validated_entries


def _is_allowed_archive_path(relative: PurePosixPath, *, include_private: bool) -> bool:
    value = relative.as_posix()
    if relative.parts and relative.parts[0] in DEFAULT_DATA_DIRECTORIES:
        return True
    if value in SAFE_CONFIG_FILES:
        return True
    return include_private and value in PRIVATE_CONFIG_FILES


def _validated_relative_path(value: Any) -> PurePosixPath:
    text = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UserDataTransferError(f"迁移包路径不安全：{value}")
    if path.parts[0].endswith(":"):
        raise UserDataTransferError(f"迁移包路径不安全：{value}")
    return path


def _safe_target_path(root: Path, relative: PurePosixPath) -> Path:
    target = root.joinpath(*relative.parts)
    root_resolved = root.resolve()
    target_resolved = target.resolve(strict=False)
    if not _is_within(target_resolved, root_resolved):
        raise UserDataTransferError(f"目标路径越界：{relative.as_posix()}")
    current = root_resolved
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and _is_link_or_reparse(current):
            raise UserDataTransferError(f"目标路径包含链接或重解析点：{current}")
    return target


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _sha256_zip_entry(archive: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(name, "r") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _non_negative_int(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise UserDataTransferError(f"迁移包字段 {field} 必须是整数。") from exc
    if parsed < 0:
        raise UserDataTransferError(f"迁移包字段 {field} 不能为负数。")
    return parsed
