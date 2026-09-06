"""Verify that the active Python 3.12 environment matches a compiled lock."""

from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import sys
from typing import Callable


PIN_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock_pins(path: Path) -> dict[str, tuple[str, str]]:
    """Return normalized name -> (display name, version) from a pip-compile lock."""
    pins: dict[str, tuple[str, str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        match = PIN_PATTERN.match(raw_line.strip())
        if not match:
            continue
        display_name, version = match.groups()
        key = normalize_name(display_name)
        previous = pins.get(key)
        if previous is not None and previous[1] != version:
            raise ValueError(
                f"锁文件包含冲突版本: {previous[0]}=={previous[1]} / "
                f"{display_name}=={version}"
            )
        pins[key] = (display_name, version)
    if not pins:
        raise ValueError("锁文件中没有找到 name==version 依赖")
    return pins


def validate_environment(
    lock_path: Path,
    *,
    python_minor: str,
    current_python: tuple[int, int] | None = None,
    version_getter: Callable[[str], str] = metadata.version,
) -> list[str]:
    errors: list[str] = []
    actual_python = current_python or (sys.version_info.major, sys.version_info.minor)
    actual_minor = f"{actual_python[0]}.{actual_python[1]}"
    if actual_minor != python_minor:
        errors.append(f"Python 版本不匹配: 需要 {python_minor}，当前 {actual_minor}")

    try:
        pins = read_lock_pins(lock_path)
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(f"无法读取依赖锁 {lock_path}: {exc}")
        return errors

    for normalized_name, (display_name, expected) in sorted(pins.items()):
        try:
            actual = version_getter(normalized_name)
        except metadata.PackageNotFoundError:
            errors.append(f"缺少依赖: {display_name}=={expected}")
            continue
        if actual != expected:
            errors.append(
                f"依赖版本不匹配: {display_name} 需要 {expected}，当前 {actual}"
            )
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查当前环境是否符合依赖锁")
    parser.add_argument("--lock", type=Path, required=True, help="pip-compile 生成的锁文件")
    parser.add_argument(
        "--python-minor",
        default="3.12",
        help="要求的 Python 主次版本，默认 3.12",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    errors = validate_environment(
        args.lock.resolve(),
        python_minor=args.python_minor,
    )
    if errors:
        print("依赖锁检查失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    pins = read_lock_pins(args.lock.resolve())
    print(f"依赖锁一致：Python {args.python_minor}，{len(pins)} 个固定版本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
