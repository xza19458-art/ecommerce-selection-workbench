from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.user_data_transfer import (  # noqa: E402
    DATA_PREFIX,
    MANIFEST_NAME,
    UserDataTransferError,
    create_user_data_backup,
    inspect_user_data_archive,
    restore_user_data_backup,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sample_root(root: Path) -> None:
    _write(root / "html" / "squishy" / "page1.html", "<html>real evidence</html>")
    _write(root / "reviews" / "B0TEST.json", '{"rating": 1}')
    _write(root / "exports" / "products.csv", "asin,title\nB0TEST,Example\n")
    _write(root / "数据结果" / "result.json", '{"score": 70}')
    _write(root / "data_warehouse" / "warehouse_manifest.json", '{"tables": {}}')
    _write(root / "config" / "settings.json", '{"ui": {"theme": "dark"}}')
    _write(root / "config" / "crawl_queues.json", '{"queues": []}')
    _write(root / "config" / "database.json", '{"password": "local-secret"}')
    _write(root / "config" / "agent.json", '{"api_key": "agent-secret"}')
    _write(root / "logs" / "desktop.log", "private log")
    _write(root / "webview_state" / "Cookies", "session")
    _write(root / "cache" / "image.jpg", "cache")


def test_safe_backup_includes_evidence_and_excludes_private_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _sample_root(source)
    archive = tmp_path / "safe.zip"

    summary = create_user_data_backup(source_root=source, output_path=archive)
    inspected = inspect_user_data_archive(archive)

    assert summary.file_count == 7
    assert inspected["file_count"] == 7
    assert inspected["includes_private_config"] is False
    assert inspected["mysql_included"] is False
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert DATA_PREFIX + "html/squishy/page1.html" in names
    assert DATA_PREFIX + "config/settings.json" in names
    assert DATA_PREFIX + "config/database.json" not in names
    assert not any("webview_state" in name or "/logs/" in name for name in names)


def test_private_config_requires_explicit_backup_mode(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _sample_root(source)
    archive = tmp_path / "private.zip"

    summary = create_user_data_backup(
        source_root=source,
        output_path=archive,
        include_private_config=True,
    )

    assert summary.includes_private_config is True
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert DATA_PREFIX + "config/database.json" in names
    assert DATA_PREFIX + "config/agent.json" in names
    assert not any("webview_state" in name for name in names)


def test_restore_is_idempotent_and_overwrite_preserves_original(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _sample_root(source)
    archive = tmp_path / "safe.zip"
    create_user_data_backup(source_root=source, output_path=archive)
    target = tmp_path / "target"

    first = restore_user_data_backup(archive, target_root=target)
    second = restore_user_data_backup(archive, target_root=target)

    assert first.written_count == 7
    assert second.written_count == 0
    assert second.unchanged_count == 7

    conflict = target / "html" / "squishy" / "page1.html"
    conflict.write_text("local change", encoding="utf-8")
    with pytest.raises(UserDataTransferError, match="默认不覆盖"):
        restore_user_data_backup(archive, target_root=target)

    restored = restore_user_data_backup(
        archive,
        target_root=target,
        overwrite=True,
        now=datetime(2026, 7, 24, 17, 0, 0),
    )

    assert restored.overwritten_count == 1
    assert conflict.read_text(encoding="utf-8") == "<html>real evidence</html>"
    assert restored.backup_dir is not None
    original = restored.backup_dir / "html" / "squishy" / "page1.html"
    assert original.read_text(encoding="utf-8") == "local change"


def test_tampered_or_traversal_archive_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _sample_root(source)
    original = tmp_path / "original.zip"
    tampered = tmp_path / "tampered.zip"
    create_user_data_backup(source_root=source, output_path=original)

    with zipfile.ZipFile(original) as reader, zipfile.ZipFile(tampered, "w") as writer:
        for name in reader.namelist():
            payload = reader.read(name)
            if name == DATA_PREFIX + "html/squishy/page1.html":
                payload = b"changed"
            writer.writestr(name, payload)
    with pytest.raises(UserDataTransferError, match="大小不一致|哈希不一致"):
        inspect_user_data_archive(tampered)

    traversal = tmp_path / "traversal.zip"
    manifest = {
        "schema": "amazon-selection-local-data-v1",
        "includes_private_config": False,
        "mysql_included": False,
        "files": [
            {
                "path": "../outside.txt",
                "size": 1,
                "sha256": "0" * 64,
            }
        ],
    }
    with zipfile.ZipFile(traversal, "w") as writer:
        writer.writestr(MANIFEST_NAME, json.dumps(manifest))
        writer.writestr("data/../outside.txt", "x")
    with pytest.raises(UserDataTransferError, match="路径不安全"):
        restore_user_data_backup(traversal, target_root=tmp_path / "restore")


def test_backup_cannot_be_created_inside_source_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(UserDataTransferError, match="不能写在用户数据目录内部"):
        create_user_data_backup(
            source_root=source,
            output_path=source / "backup.zip",
        )


def test_restore_never_overwrites_existing_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write(source / "html" / "page.html", "evidence")
    archive = tmp_path / "safe.zip"
    create_user_data_backup(source_root=source, output_path=archive)
    target = tmp_path / "target"
    (target / "html" / "page.html").mkdir(parents=True)

    with pytest.raises(UserDataTransferError, match="不是普通文件"):
        restore_user_data_backup(
            archive,
            target_root=target,
            overwrite=True,
        )
