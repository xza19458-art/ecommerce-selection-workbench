from __future__ import annotations

from importlib import metadata
from pathlib import Path

from scripts import check_dependency_lock


def test_read_lock_pins_parses_pip_compile_output(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text(
        "# generated\n"
        "FastAPI==0.137.2 \\\n"
        "    --hash=sha256:abc\n"
        "typing_extensions==4.15.0 ; python_version >= '3.12'\n",
        encoding="utf-8",
    )

    pins = check_dependency_lock.read_lock_pins(lock)

    assert pins == {
        "fastapi": ("FastAPI", "0.137.2"),
        "typing-extensions": ("typing_extensions", "4.15.0"),
    }


def test_validate_environment_reports_python_missing_and_version_errors(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("alpha==1.0\nbeta==2.0\n", encoding="utf-8")

    def version_getter(name: str) -> str:
        if name == "alpha":
            return "0.9"
        raise metadata.PackageNotFoundError(name)

    errors = check_dependency_lock.validate_environment(
        lock,
        python_minor="3.12",
        current_python=(3, 11),
        version_getter=version_getter,
    )

    assert any("Python 版本不匹配" in error for error in errors)
    assert any("alpha 需要 1.0" in error for error in errors)
    assert any("缺少依赖: beta==2.0" in error for error in errors)


def test_validate_environment_accepts_matching_environment(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("alpha==1.0\n", encoding="utf-8")

    errors = check_dependency_lock.validate_environment(
        lock,
        python_minor="3.12",
        current_python=(3, 12),
        version_getter=lambda _name: "1.0",
    )

    assert errors == []
