from __future__ import annotations

import json
from pathlib import Path

import pytest

from database.config_bootstrap import (
    DatabaseConfigPreparationError,
    prepare_database_config,
)


def _write_config(path: Path, *, password: str = "secret") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 3306,
                "user": "root",
                "password": password,
                "database": "amazon_selection",
                "charset": "utf8mb4",
            }
        ),
        encoding="utf-8",
    )


def test_packaged_config_migrates_valid_file_from_internal(tmp_path: Path) -> None:
    target = tmp_path / "package" / "config" / "database.json"
    legacy = tmp_path / "package" / "_internal" / "config" / "database.json"
    template = tmp_path / "package" / "_internal" / "config" / "database.example.json"
    _write_config(legacy, password="existing-password")
    _write_config(template, password="your_password")

    result = prepare_database_config(
        frozen=True,
        target_path=target,
        legacy_path=legacy,
        template_path=template,
    )

    assert result.status == "migrated"
    assert result.path == target.resolve()
    assert target.read_bytes() == legacy.read_bytes()


def test_fresh_package_creates_editable_config_from_example(tmp_path: Path) -> None:
    target = tmp_path / "package" / "config" / "database.json"
    legacy = tmp_path / "package" / "_internal" / "config" / "database.json"
    template = tmp_path / "package" / "_internal" / "config" / "database.example.json"
    _write_config(template, password="your_password")

    result = prepare_database_config(
        frozen=True,
        target_path=target,
        legacy_path=legacy,
        template_path=template,
    )

    assert result.status == "created"
    assert target.read_bytes() == template.read_bytes()


def test_source_mode_does_not_create_missing_config(tmp_path: Path) -> None:
    target = tmp_path / "config" / "database.json"

    result = prepare_database_config(frozen=False, target_path=target)

    assert result.status == "missing"
    assert not target.exists()


def test_invalid_misplaced_config_is_not_silently_copied(tmp_path: Path) -> None:
    target = tmp_path / "package" / "config" / "database.json"
    legacy = tmp_path / "package" / "_internal" / "config" / "database.json"
    template = tmp_path / "package" / "_internal" / "config" / "database.example.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("not-json", encoding="utf-8")
    _write_config(template)

    with pytest.raises(DatabaseConfigPreparationError, match="有效 JSON"):
        prepare_database_config(
            frozen=True,
            target_path=target,
            legacy_path=legacy,
            template_path=template,
        )

    assert not target.exists()
