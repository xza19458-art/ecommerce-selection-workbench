from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.distribution_package import (  # noqa: E402
    DistributionPackageError,
    build_clean_distribution,
    verify_clean_distribution,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sample_onedir(root: Path) -> None:
    _write(root / "AmazonSelectionWorkbench.exe", "fake executable")
    _write(root / "_internal" / "web" / "index.html", "<html>app</html>")
    _write(root / "_internal" / "config" / "database.example.json", '{"host": "localhost"}')
    _write(root / "config" / "database.json", '{"password": "must-not-ship"}')
    _write(root / "html" / "real.html", "real local evidence")
    _write(root / "logs" / "desktop.log", "private log")


def test_build_copies_only_program_allowlist_and_creates_verified_zip(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dist" / "AmazonSelectionWorkbench"
    _sample_onedir(source)
    guide = tmp_path / "DEPLOYMENT_GUIDE.md"
    guide.write_text("# Guide", encoding="utf-8")
    output = tmp_path / "release" / "AmazonSelectionWorkbench_clean"

    summary = build_clean_distribution(
        source_dir=source,
        output_dir=output,
        guide_path=guide,
        now=datetime(2026, 7, 24, 17, 0, 0),
    )
    verified = verify_clean_distribution(output)

    assert {path.name for path in output.iterdir()} == {
        "AmazonSelectionWorkbench.exe",
        "_internal",
        "首次部署与迁移说明.md",
        "distribution_manifest.json",
    }
    assert not (output / "config").exists()
    assert not (output / "html").exists()
    assert verified["clean"] is True
    assert verified["executable_sha256"] == summary.executable_sha256
    assert summary.zip_path is not None and summary.zip_path.is_file()
    with zipfile.ZipFile(summary.zip_path) as archive:
        names = set(archive.namelist())
    assert "AmazonSelectionWorkbench_clean/AmazonSelectionWorkbench.exe" in names
    assert not any("/html/" in name or "/config/database.json" in name for name in names)


def test_real_internal_config_blocks_distribution(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    _sample_onedir(source)
    _write(source / "_internal" / "config" / "database.json", '{"password": "secret"}')
    guide = tmp_path / "guide.md"
    guide.write_text("# Guide", encoding="utf-8")

    with pytest.raises(DistributionPackageError, match="真实配置"):
        build_clean_distribution(
            source_dir=source,
            output_dir=tmp_path / "release",
            guide_path=guide,
        )


def test_modified_distribution_fails_hash_verification(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    _sample_onedir(source)
    guide = tmp_path / "guide.md"
    guide.write_text("# Guide", encoding="utf-8")
    output = tmp_path / "release"
    build_clean_distribution(
        source_dir=source,
        output_dir=output,
        guide_path=guide,
        create_zip=False,
    )

    (output / "_internal" / "web" / "index.html").write_text(
        "<html>tampered</html>",
        encoding="utf-8",
    )

    with pytest.raises(DistributionPackageError, match="哈希不一致"):
        verify_clean_distribution(output)
