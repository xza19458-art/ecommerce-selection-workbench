"""Run the local G0 acceptance gate without contacting Amazon.

The full profile checks source quality, tests, real-database readiness, disposable
MySQL migrations, read-only application APIs, and desktop/package startup.  It
never invokes a collection endpoint or opens Chrome.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Callable, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
PYTHON = str(Path(sys.executable).resolve())
HOST = "127.0.0.1"
PACKAGE_ARTIFACT = ROOT / "dist" / "AmazonSelectionWorkbench" / "AmazonSelectionWorkbench.exe"
RUNTIME_TEMP = ROOT / ".quality_gate_tmp"
PACKAGE_SOURCE_PATHS = (
    ROOT / "AmazonSelectionWorkbench.spec",
    ROOT / "requirements.lock.txt",
    ROOT / "requirements-dev.lock.txt",
    ROOT / "desktop_app.py",
    ROOT / "main.py",
    ROOT / "pkg_paths.py",
    ROOT / "api",
    ROOT / "core",
    ROOT / "services",
    ROOT / "repositories",
    ROOT / "analysis",
    ROOT / "database",
    ROOT / "parsers",
    ROOT / "web",
    ROOT / "config" / "database.example.json",
    ROOT / "config" / "warehouse.example.json",
    ROOT / "config" / "translation.example.json",
    ROOT / "config" / "agent.example.json",
)
PACKAGE_SOURCE_SUFFIXES = {".css", ".html", ".ico", ".js", ".json", ".py", ".sql", ".toml"}

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True)
class GateStage:
    name: str
    kind: str
    command: tuple[str, ...] = ()
    timeout: float = 300.0
    detail: str = ""


@dataclass(frozen=True)
class GateResult:
    name: str
    status: str
    detail: str
    elapsed_seconds: float


def _new_pytest_basetemp() -> Path:
    return RUNTIME_TEMP / f"pytest-{os.getpid()}-{uuid.uuid4().hex}"


def build_stages(*, quick: bool = False) -> list[GateStage]:
    pytest_basetemp = _new_pytest_basetemp()
    stages = [
        GateStage("Git 空白错误", "command", ("git", "diff", "--check"), 60),
        GateStage(
            "Python 3.12 依赖锁",
            "command",
            (
                PYTHON,
                "scripts/check_dependency_lock.py",
                "--lock",
                "requirements-dev.lock.txt",
                "--python-minor",
                "3.12",
            ),
            60,
        ),
        GateStage("Ruff 静态检查", "command", (PYTHON, "-m", "ruff", "check", "."), 180),
        GateStage(
            "Python 编译检查",
            "command",
            (
                PYTHON,
                "-m",
                "compileall",
                "-q",
                "api",
                "core",
                "database",
                "parsers",
                "repositories",
                "services",
                "scripts",
                "tests",
            ),
            180,
        ),
        GateStage("前端语法检查", "command", ("node", "--check", "web/app.js"), 60),
        GateStage(
            "全量 Python 测试",
            "command",
            (
                PYTHON,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(pytest_basetemp),
            ),
            900,
        ),
    ]
    if quick:
        return stages
    stages.extend(
        (
            GateStage(
                "真实库迁移状态（只读）",
                "command",
                (PYTHON, "scripts/manage_migrations.py", "--status", "--json"),
                90,
            ),
            GateStage(
                "临时 MySQL 迁移链路",
                "command",
                (PYTHON, "scripts/migration_mysql_smoke.py"),
                300,
                "只操作并删除 amazon_selection_migration_smoke_ 前缀临时库",
            ),
            GateStage(
                "真实数据健康检查（只读）",
                "command",
                (PYTHON, "scripts/smoke_check.py"),
                300,
            ),
            GateStage("主要 API 契约（只读）", "api", timeout=360),
            GateStage(
                "桌面壳源码 smoke",
                "command",
                (PYTHON, "desktop_app.py", "--smoke"),
                180,
            ),
            GateStage("桌面打包产物 smoke", "package", timeout=240),
        )
    )
    return stages


def _child_environment() -> dict[str, str]:
    RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["PYTHONIOENCODING"] = sys.stdout.encoding or "utf-8"
    env["TEMP"] = str(RUNTIME_TEMP)
    env["TMP"] = str(RUNTIME_TEMP)
    return env


def _display_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline(list(command))


def run_command_stage(
    stage: GateStage,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> GateResult:
    started = time.monotonic()
    print(f"\n[RUN ] {stage.name}", flush=True)
    if stage.detail:
        print(f"       {stage.detail}", flush=True)
    print(f"       {_display_command(stage.command)}", flush=True)
    try:
        completed = runner(
            list(stage.command),
            cwd=str(ROOT),
            env=_child_environment(),
            timeout=stage.timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return _result(stage.name, FAIL, f"命令不存在: {exc.filename}", started)
    except subprocess.TimeoutExpired:
        return _result(stage.name, FAIL, f"超过 {stage.timeout:.0f} 秒未完成", started)
    except OSError as exc:
        return _result(stage.name, FAIL, f"无法启动命令: {exc}", started)
    if completed.returncode != 0:
        return _result(stage.name, FAIL, f"退出码 {completed.returncode}", started)
    return _result(stage.name, PASS, "通过", started)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def _read_http_error(exc: HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"HTTP {exc.code}"
    return str(payload.get("message") or f"HTTP {exc.code}")


def _wait_for_api_readiness(
    base_url: str,
    process: subprocess.Popen,
    *,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last_message = "本地 API 尚未响应"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            return False, f"本地 API 提前退出，退出码 {return_code}"
        try:
            with urlopen(f"{base_url}/api/ready", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            report = payload.get("data") or {}
            if response.status == 200 and payload.get("ok") is True and report.get("ready"):
                return True, str(report.get("message") or "数据库已就绪")
            last_message = str(payload.get("message") or report.get("message") or last_message)
        except HTTPError as exc:
            last_message = _read_http_error(exc)
        except (URLError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_message = str(exc)
        time.sleep(0.25)
    return False, last_message


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _log_tail(log_file, *, limit: int = 4_000) -> str:
    try:
        log_file.flush()
        log_file.seek(0)
        content = log_file.read().decode("utf-8", "replace").strip()
    except (OSError, ValueError):
        return ""
    return content[-limit:]


def run_api_stage(stage: GateStage) -> GateResult:
    """Start an isolated local API and run GET-only smoke checks against it."""
    started = time.monotonic()
    port = _find_free_port()
    base_url = f"http://{HOST}:{port}"
    command = (
        PYTHON,
        "-m",
        "uvicorn",
        "api.app:app",
        "--host",
        HOST,
        "--port",
        str(port),
        "--log-level",
        "warning",
    )
    print(f"\n[RUN ] {stage.name}", flush=True)
    print(f"       临时地址 {base_url}；不调用采集、图片或写库接口", flush=True)
    process: subprocess.Popen | None = None
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    RUNTIME_TEMP.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(mode="w+b", dir=RUNTIME_TEMP) as api_log:
        try:
            process = subprocess.Popen(
                list(command),
                cwd=str(ROOT),
                env=_child_environment(),
                stdout=api_log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            ready, message = _wait_for_api_readiness(base_url, process)
            if not ready:
                tail = _log_tail(api_log)
                detail = f"启动或就绪失败: {message}"
                if tail:
                    detail += f"；日志末尾: {tail}"
                return _result(stage.name, FAIL, detail, started)
            smoke_command = (
                PYTHON,
                "scripts/api_readonly_smoke.py",
                "--base-url",
                base_url,
                "--timeout",
                "30",
            )
            completed = subprocess.run(
                list(smoke_command),
                cwd=str(ROOT),
                env=_child_environment(),
                timeout=stage.timeout,
                check=False,
            )
            if completed.returncode != 0:
                return _result(stage.name, FAIL, f"只读 API smoke 退出码 {completed.returncode}", started)
            return _result(stage.name, PASS, message, started)
        except FileNotFoundError as exc:
            return _result(stage.name, FAIL, f"命令不存在: {exc.filename}", started)
        except subprocess.TimeoutExpired:
            return _result(stage.name, FAIL, f"超过 {stage.timeout:.0f} 秒未完成", started)
        except OSError as exc:
            return _result(stage.name, FAIL, f"本地 API 无法启动: {exc}", started)
        finally:
            if process is not None:
                _stop_process(process)


def _iter_package_sources(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_file():
            yield path
            continue
        if not path.is_dir():
            continue
        for child in path.rglob("*"):
            if not child.is_file() or "__pycache__" in child.parts:
                continue
            if child.suffix.lower() in PACKAGE_SOURCE_SUFFIXES:
                yield child


def inspect_package_artifact(
    artifact: Path = PACKAGE_ARTIFACT,
    *,
    source_paths: Iterable[Path] = PACKAGE_SOURCE_PATHS,
) -> tuple[str, str]:
    if not artifact.is_file():
        return "missing", f"未找到 {artifact.relative_to(ROOT) if artifact.is_relative_to(ROOT) else artifact}"
    sources = list(_iter_package_sources(source_paths))
    if not sources:
        return "current", "未找到可比较源码，按现有产物执行"
    newest = max(sources, key=lambda path: path.stat().st_mtime)
    if artifact.stat().st_mtime + 1 < newest.stat().st_mtime:
        newest_label = newest.relative_to(ROOT) if newest.is_relative_to(ROOT) else newest
        return "stale", f"产物早于源码 {newest_label}，需重新打包"
    return "current", "产物时间不早于当前打包输入"


def run_package_stage(
    stage: GateStage,
    *,
    require_package: bool,
    skip_package: bool,
    artifact: Path = PACKAGE_ARTIFACT,
    source_paths: Iterable[Path] = PACKAGE_SOURCE_PATHS,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> GateResult:
    started = time.monotonic()
    if skip_package:
        print(f"\n[RUN ] {stage.name}", flush=True)
        return _result(stage.name, SKIP, "命令行已指定 --skip-package", started)
    state, detail = inspect_package_artifact(artifact, source_paths=source_paths)
    if state != "current":
        print(f"\n[RUN ] {stage.name}", flush=True)
        status = FAIL if require_package else SKIP
        return _result(stage.name, status, detail, started)
    package_stage = GateStage(
        stage.name,
        "command",
        (str(artifact), "--smoke"),
        stage.timeout,
        detail,
    )
    return run_command_stage(package_stage, runner=runner)


def _result(name: str, status: str, detail: str, started: float) -> GateResult:
    return GateResult(name, status, detail, round(time.monotonic() - started, 3))


def run_gate(
    stages: Iterable[GateStage],
    *,
    require_package: bool = False,
    skip_package: bool = False,
) -> list[GateResult]:
    results: list[GateResult] = []
    for stage in stages:
        if stage.kind == "command":
            result = run_command_stage(stage)
        elif stage.kind == "api":
            result = run_api_stage(stage)
        elif stage.kind == "package":
            result = run_package_stage(
                stage,
                require_package=require_package,
                skip_package=skip_package,
            )
        else:
            result = GateResult(stage.name, FAIL, f"未知阶段类型: {stage.kind}", 0.0)
        results.append(result)
        print(
            f"[{result.status:4}] {result.name}: {result.detail} "
            f"({result.elapsed_seconds:.1f}s)",
            flush=True,
        )
        if result.status == FAIL:
            print("       已停止后续阶段；先修复当前失败再继续。", flush=True)
            break
    return results


def _write_report(path: Path, *, mode: str, started_at: str, results: list[GateResult]) -> None:
    report_path = path if path.is_absolute() else ROOT / path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "passed": sum(result.status == PASS for result in results),
            "failed": sum(result.status == FAIL for result in results),
            "skipped": sum(result.status == SKIP for result in results),
        },
        "results": [asdict(result) for result in results],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"验收报告已写入: {report_path}")


def _print_stage_list(stages: Iterable[GateStage]) -> None:
    for index, stage in enumerate(stages, start=1):
        command = _display_command(stage.command) if stage.command else stage.kind
        print(f"{index:>2}. {stage.name}: {command}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行不访问 Amazon 的 G0 本地统一验收门禁",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="只运行 Git/Ruff/编译/前端语法/全量测试，不连接 MySQL 或启动服务",
    )
    package_group = parser.add_mutually_exclusive_group()
    package_group.add_argument(
        "--require-package",
        action="store_true",
        help="要求当前打包产物存在、未过期且 smoke 通过；用于发布前验收",
    )
    package_group.add_argument(
        "--skip-package",
        action="store_true",
        help="跳过打包产物 smoke；桌面壳源码 smoke 仍会执行",
    )
    parser.add_argument("--list", action="store_true", help="只列出阶段，不执行")
    parser.add_argument("--report-json", type=Path, help="可选：将结果写入指定 JSON")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.quick and args.require_package:
        parser.error("--quick 与 --require-package 不能同时使用")
    stages = build_stages(quick=args.quick)
    if args.list:
        _print_stage_list(stages)
        return 0

    mode = "quick" if args.quick else "full"
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    print("Amazon 选品系统 G0 统一验收")
    print(f"模式: {mode}；代码目录: {ROOT}")
    print("边界: 不打开 Chrome、不访问 Amazon、不调用采集或业务写入 API。")
    try:
        results = run_gate(
            stages,
            require_package=args.require_package,
            skip_package=args.skip_package,
        )
    except KeyboardInterrupt:
        print("\n验收已由用户中止。")
        return 130

    passed = sum(result.status == PASS for result in results)
    failed = sum(result.status == FAIL for result in results)
    skipped = sum(result.status == SKIP for result in results)
    print("\n" + "=" * 64)
    print(f"结果: PASS={passed} FAIL={failed} SKIP={skipped}")
    if failed:
        print("QUALITY_GATE_FAIL")
    elif skipped:
        print("QUALITY_GATE_OK（存在明确跳过项；发布前请使用 --require-package）")
    else:
        print("QUALITY_GATE_OK")
    if args.report_json:
        _write_report(args.report_json, mode=mode, started_at=started_at, results=results)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
