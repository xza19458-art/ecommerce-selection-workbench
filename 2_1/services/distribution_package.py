"""Build and verify a clean onedir distribution from a tested PyInstaller build."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from typing import Any, Iterable
import uuid
import zipfile


DISTRIBUTION_SCHEMA = "amazon-selection-clean-distribution-v1"
EXECUTABLE_NAME = "AmazonSelectionWorkbench.exe"
INTERNAL_DIRECTORY = "_internal"
GUIDE_NAME = "首次部署与迁移说明.md"
MANIFEST_NAME = "distribution_manifest.json"
ALLOWED_TOP_LEVEL = {
    EXECUTABLE_NAME,
    INTERNAL_DIRECTORY,
    GUIDE_NAME,
    MANIFEST_NAME,
}
FORBIDDEN_USER_DIRECTORIES = {
    "config",
    "html",
    "reviews",
    "exports",
    "数据结果",
    "data_warehouse",
    "logs",
    "cache",
    "drivers",
    ".argos",
    "webview_state",
}
FORBIDDEN_CONFIG_NAMES = {
    "database.json",
    "agent.json",
    "translation.json",
    "warehouse.json",
    "settings.json",
    "crawl_queues.json",
}


class DistributionPackageError(ValueError):
    """Raised when a clean distribution cannot be built or verified."""


@dataclass(frozen=True)
class DistributionSummary:
    output_dir: Path
    manifest_path: Path
    manifest_sha256: str
    executable_sha256: str
    file_count: int
    total_bytes: int
    zip_path: Path | None = None
    zip_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "executable_sha256": self.executable_sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "zip_path": str(self.zip_path) if self.zip_path else None,
            "zip_sha256": self.zip_sha256,
            "message": (
                f"干净分发包已生成，共 {self.file_count} 个程序文件；"
                "不包含 MySQL、真实配置、HTML、日志、缓存或浏览器状态。"
            ),
        }


def default_distribution_path(
    *,
    release_root: str | Path = "release",
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S")
    return Path(release_root) / f"AmazonSelectionWorkbench_{timestamp}"


def build_clean_distribution(
    *,
    source_dir: str | Path,
    output_dir: str | Path,
    guide_path: str | Path,
    create_zip: bool = True,
    now: datetime | None = None,
) -> DistributionSummary:
    source_input = Path(source_dir)
    guide_input = Path(guide_path)
    if source_input.exists() and _is_link_or_reparse(source_input):
        raise DistributionPackageError(f"onedir 源目录不能是链接或重解析点：{source_input}")
    if guide_input.exists() and _is_link_or_reparse(guide_input):
        raise DistributionPackageError(f"部署说明不能是链接或重解析点：{guide_input}")
    source = source_input.resolve()
    destination = Path(output_dir).resolve()
    guide = guide_input.resolve()
    _validate_source(source, guide)
    if destination.exists():
        raise DistributionPackageError(f"目标分发目录已存在：{destination}")
    if _is_within(destination, source) or _is_within(source, destination):
        raise DistributionPackageError("源目录与目标目录不能互相包含。")

    zip_path = destination.with_suffix(".zip") if create_zip else None
    if zip_path and zip_path.exists():
        raise DistributionPackageError(f"目标 ZIP 已存在：{zip_path}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        stage.mkdir()
        shutil.copy2(source / EXECUTABLE_NAME, stage / EXECUTABLE_NAME)
        shutil.copytree(source / INTERNAL_DIRECTORY, stage / INTERNAL_DIRECTORY)
        shutil.copy2(guide, stage / GUIDE_NAME)
        _validate_program_tree(stage, expect_manifest=False)

        entries = _file_entries(stage)
        executable_sha256 = next(
            entry["sha256"] for entry in entries if entry["path"] == EXECUTABLE_NAME
        )
        manifest = {
            "schema": DISTRIBUTION_SCHEMA,
            "created_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
            "source_artifact": source.name,
            "clean_distribution": True,
            "mysql_included": False,
            "user_data_included": False,
            "browser_session_included": False,
            "executable_sha256": executable_sha256,
            "files": entries,
        }
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(stage, destination)
        verified = verify_clean_distribution(destination)

        zip_sha256: str | None = None
        if zip_path:
            temporary_zip = zip_path.with_name(f".{zip_path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with zipfile.ZipFile(
                    temporary_zip,
                    mode="x",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as archive:
                    for file_path in _walk_regular_files(destination):
                        relative = file_path.relative_to(destination.parent).as_posix()
                        archive.write(file_path, relative)
                os.replace(temporary_zip, zip_path)
                zip_sha256 = _sha256_file(zip_path)
            except Exception:
                temporary_zip.unlink(missing_ok=True)
                raise

        return DistributionSummary(
            output_dir=destination,
            manifest_path=destination / MANIFEST_NAME,
            manifest_sha256=_sha256_file(destination / MANIFEST_NAME),
            executable_sha256=str(verified["executable_sha256"]),
            file_count=int(verified["file_count"]),
            total_bytes=int(verified["total_bytes"]),
            zip_path=zip_path,
            zip_sha256=zip_sha256,
        )
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        if zip_path:
            zip_path.unlink(missing_ok=True)
        raise


def verify_clean_distribution(distribution_dir: str | Path) -> dict[str, Any]:
    root_input = Path(distribution_dir)
    if root_input.exists() and _is_link_or_reparse(root_input):
        raise DistributionPackageError(f"分发目录不能是链接或重解析点：{root_input}")
    root = root_input.resolve()
    if not root.is_dir() or _is_link_or_reparse(root):
        raise DistributionPackageError(f"分发目录不存在或不安全：{root}")
    _validate_program_tree(root, expect_manifest=True)

    manifest_path = root / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionPackageError(f"分发清单无法读取：{manifest_path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != DISTRIBUTION_SCHEMA:
        raise DistributionPackageError("分发清单 schema 不受支持。")
    if (
        manifest.get("clean_distribution") is not True
        or manifest.get("mysql_included") is not False
        or manifest.get("user_data_included") is not False
        or manifest.get("browser_session_included") is not False
    ):
        raise DistributionPackageError("分发清单的数据边界声明不完整。")

    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list):
        raise DistributionPackageError("分发清单文件列表格式错误。")
    expected_paths = {
        path.relative_to(root).as_posix()
        for path in _walk_regular_files(root)
        if path.name != MANIFEST_NAME
    }
    listed_paths: set[str] = set()
    total_bytes = 0
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise DistributionPackageError("分发清单包含无效文件项。")
        relative = _validated_relative_path(raw.get("path"))
        path_text = relative.as_posix()
        if path_text in listed_paths:
            raise DistributionPackageError(f"分发清单包含重复路径：{path_text}")
        listed_paths.add(path_text)
        target = _safe_path(root, relative)
        if not target.is_file() or _is_link_or_reparse(target):
            raise DistributionPackageError(f"分发文件不存在或不安全：{path_text}")
        size = _non_negative_int(raw.get("size"), field="size")
        digest = _validated_digest(raw.get("sha256"), path_text)
        if target.stat().st_size != size or _sha256_file(target) != digest:
            raise DistributionPackageError(f"分发文件大小或哈希不一致：{path_text}")
        total_bytes += size
    if listed_paths != expected_paths:
        missing = sorted(expected_paths - listed_paths)
        extra = sorted(listed_paths - expected_paths)
        raise DistributionPackageError(
            f"分发清单与目录不一致；未登记 {missing[:3]}，不存在 {extra[:3]}。"
        )

    executable_digest = _sha256_file(root / EXECUTABLE_NAME)
    if str(manifest.get("executable_sha256") or "").upper() != executable_digest:
        raise DistributionPackageError("分发清单中的 EXE 哈希不一致。")
    return {
        "distribution_dir": str(root),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "executable_sha256": executable_digest,
        "file_count": len(raw_entries),
        "total_bytes": total_bytes,
        "clean": True,
    }


def _validate_source(source: Path, guide: Path) -> None:
    if not source.is_dir() or _is_link_or_reparse(source):
        raise DistributionPackageError(f"未找到安全的 onedir 源目录：{source}")
    executable = source / EXECUTABLE_NAME
    internal = source / INTERNAL_DIRECTORY
    if not executable.is_file() or not internal.is_dir():
        raise DistributionPackageError(
            f"源目录必须包含 {EXECUTABLE_NAME} 与 {INTERNAL_DIRECTORY}/。"
        )
    if _is_link_or_reparse(executable) or _is_link_or_reparse(internal):
        raise DistributionPackageError("源程序路径不能是链接或重解析点。")
    if not guide.is_file() or _is_link_or_reparse(guide):
        raise DistributionPackageError(f"未找到安全的部署说明：{guide}")
    _validate_internal_config(internal)
    for _ in _walk_regular_files(internal):
        pass


def _validate_program_tree(root: Path, *, expect_manifest: bool) -> None:
    names = {path.name for path in root.iterdir()}
    expected = set(ALLOWED_TOP_LEVEL)
    if not expect_manifest:
        expected.remove(MANIFEST_NAME)
    unexpected = sorted(names - expected)
    missing = sorted(expected - names)
    if unexpected or missing:
        raise DistributionPackageError(
            f"分发根目录结构不符合白名单；多余 {unexpected}，缺少 {missing}。"
        )
    for forbidden in FORBIDDEN_USER_DIRECTORIES:
        if (root / forbidden).exists():
            raise DistributionPackageError(f"分发包包含用户数据目录：{forbidden}")
    _validate_internal_config(root / INTERNAL_DIRECTORY)
    for _ in _walk_regular_files(root):
        pass


def _validate_internal_config(internal: Path) -> None:
    config_dir = internal / "config"
    if not config_dir.exists():
        return
    if not config_dir.is_dir() or _is_link_or_reparse(config_dir):
        raise DistributionPackageError("分发资源中的 config 路径不安全。")
    for path in config_dir.iterdir():
        if _is_link_or_reparse(path):
            raise DistributionPackageError(f"分发资源包含链接：{path}")
        if path.is_dir():
            raise DistributionPackageError(f"分发资源 config 只允许示例配置文件：{path.name}")
        if path.is_file() and path.name in FORBIDDEN_CONFIG_NAMES:
            raise DistributionPackageError(f"分发资源包含真实配置：{path.name}")
        if path.is_file() and path.suffix.casefold() == ".json" and not path.name.endswith(
            ".example.json"
        ):
            raise DistributionPackageError(f"分发资源包含非示例 JSON 配置：{path.name}")


def _file_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(_walk_regular_files(root), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        entries.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return entries


def _walk_regular_files(directory: Path) -> Iterable[Path]:
    for current_root, dir_names, file_names in os.walk(directory, followlinks=False):
        current = Path(current_root)
        for dir_name in dir_names:
            child = current / dir_name
            if _is_link_or_reparse(child):
                raise DistributionPackageError(f"分发目录包含链接或重解析点：{child}")
        for file_name in file_names:
            child = current / file_name
            if _is_link_or_reparse(child) or not child.is_file():
                raise DistributionPackageError(f"分发目录包含非普通文件：{child}")
            yield child


def _validated_relative_path(value: Any) -> PurePosixPath:
    text = str(value or "").replace("\\", "/").strip()
    relative = PurePosixPath(text)
    if (
        not text
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.parts[0].endswith(":")
    ):
        raise DistributionPackageError(f"分发清单路径不安全：{value}")
    return relative


def _safe_path(root: Path, relative: PurePosixPath) -> Path:
    target = root.joinpath(*relative.parts)
    try:
        target.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise DistributionPackageError(f"分发清单路径越界：{relative.as_posix()}") from exc
    return target


def _validated_digest(value: Any, path_text: str) -> str:
    digest = str(value or "").upper()
    if len(digest) != 64 or any(char not in "0123456789ABCDEF" for char in digest):
        raise DistributionPackageError(f"分发文件哈希格式错误：{path_text}")
    return digest


def _non_negative_int(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DistributionPackageError(f"分发清单字段 {field} 必须是整数。") from exc
    if parsed < 0:
        raise DistributionPackageError(f"分发清单字段 {field} 不能为负数。")
    return parsed


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
