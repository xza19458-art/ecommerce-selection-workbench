from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from scripts import manage_migrations, quality_gate


def test_quick_gate_contains_only_local_development_checks() -> None:
    stages = quality_gate.build_stages(quick=True)

    assert [stage.kind for stage in stages] == ["command"] * 6
    commands = " ".join(" ".join(stage.command) for stage in stages)
    assert "check_dependency_lock.py" in commands
    assert "pytest" in commands
    assert "migration_mysql_smoke" not in commands
    assert "api_readonly_smoke" not in commands
    assert "desktop_app.py" not in commands


def test_repeated_gate_builds_use_distinct_pytest_basetemps() -> None:
    first_stages = quality_gate.build_stages(quick=True)
    second_stages = quality_gate.build_stages(quick=True)

    first_command = next(stage.command for stage in first_stages if "pytest" in stage.command)
    second_command = next(stage.command for stage in second_stages if "pytest" in stage.command)
    first_path = Path(first_command[first_command.index("--basetemp") + 1])
    second_path = Path(second_command[second_command.index("--basetemp") + 1])

    assert first_path.parent == quality_gate.RUNTIME_TEMP
    assert first_path.name.startswith("pytest-")
    assert first_path != second_path


def test_full_gate_includes_database_api_desktop_and_package_checks() -> None:
    stages = quality_gate.build_stages()

    names = {stage.name for stage in stages}
    kinds = {stage.kind for stage in stages}
    assert "真实库迁移状态（只读）" in names
    assert "临时 MySQL 迁移链路" in names
    assert "真实数据健康检查（只读）" in names
    assert "主要 API 契约（只读）" in names
    assert "桌面壳源码 smoke" in names
    assert "桌面打包产物 smoke" in names
    assert {"command", "api", "package"} <= kinds
    assert quality_gate.ROOT / "requirements.lock.txt" in quality_gate.PACKAGE_SOURCE_PATHS
    assert quality_gate.ROOT / "requirements-dev.lock.txt" in quality_gate.PACKAGE_SOURCE_PATHS


def test_package_artifact_status_detects_missing_stale_and_current(tmp_path: Path) -> None:
    artifact = tmp_path / "app.exe"
    source = tmp_path / "app.py"
    source.write_text("print('ok')\n", encoding="utf-8")

    state, _ = quality_gate.inspect_package_artifact(artifact, source_paths=(source,))
    assert state == "missing"

    artifact.write_bytes(b"binary")
    os.utime(artifact, (100, 100))
    os.utime(source, (200, 200))
    state, detail = quality_gate.inspect_package_artifact(artifact, source_paths=(source,))
    assert state == "stale"
    assert "需重新打包" in detail

    os.utime(artifact, (300, 300))
    state, _ = quality_gate.inspect_package_artifact(artifact, source_paths=(source,))
    assert state == "current"


def test_package_is_optional_for_development_and_required_for_release(tmp_path: Path) -> None:
    stage = quality_gate.GateStage("package", "package")
    missing = tmp_path / "missing.exe"

    optional = quality_gate.run_package_stage(
        stage,
        require_package=False,
        skip_package=False,
        artifact=missing,
        source_paths=(),
    )
    required = quality_gate.run_package_stage(
        stage,
        require_package=True,
        skip_package=False,
        artifact=missing,
        source_paths=(),
    )

    assert optional.status == quality_gate.SKIP
    assert required.status == quality_gate.FAIL


def test_command_stage_runs_from_code_root() -> None:
    calls = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    result = quality_gate.run_command_stage(
        quality_gate.GateStage("probe", "command", ("tool", "--check"), 12),
        runner=fake_runner,
    )

    assert result.status == quality_gate.PASS
    assert calls[0][0] == ["tool", "--check"]
    assert calls[0][1]["cwd"] == str(quality_gate.ROOT)
    assert calls[0][1]["timeout"] == 12
    assert calls[0][1]["check"] is False


def test_documented_migration_status_flag_is_supported() -> None:
    args = manage_migrations._parser().parse_args(["--status"])

    assert args.status is True
    assert args.apply is False
    assert args.rollback is None
