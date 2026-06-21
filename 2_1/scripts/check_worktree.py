"""工作区体检（只读）。

目的：限制"大量改动长期堆在工作区、不进 review / 不进看板"这类问题再发生
（参见 decisions/2026-06-16-双路径SQL与回退错误处理.md 约定 C）。

本脚本不修改任何文件，也不执行 git 写操作，只读取 `git status` 并给出提示：
  - 统计已改 / 未跟踪文件数；
  - 单独点名未提交的代码 / schema / 迁移文件（最该尽快提交或入看板的部分）；
  - 提醒按模块小步提交，并更新 TASK_STATUS.md。

用法（在仓库任意位置）：
    python 2_1/scripts/check_worktree.py

退出码：发现需要关注的堆积时返回 1，否则 0（方便接入手动检查或 CI）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Windows 控制台常为 GBK，输出中文/符号易崩；尽量切到 UTF-8。
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# 触发警告的阈值：可按团队习惯调整。
MAX_CHANGED_FILES = 12          # 工作区改动文件总数超过此值即提醒拆分提交
CODE_SUFFIXES = {".py", ".sql"}  # 最该尽快提交 / 入看板的"会影响行为"的文件类型


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{result.stderr.strip()}")
    return result.stdout


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    top = _git(["rev-parse", "--show-toplevel"], cwd=here.parent).strip()
    return Path(top)


def _parse_status(porcelain: str) -> tuple[list[str], list[str]]:
    """返回 (已跟踪改动文件, 未跟踪文件)。"""
    changed: list[str] = []
    untracked: list[str] = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        code, path = line[:2], line[3:]
        # 重命名形如 "old -> new"，只取目标路径
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if code == "??":
            untracked.append(path)
        else:
            changed.append(path)
    return changed, untracked


def _is_code(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_SUFFIXES


def main() -> int:
    root = _repo_root()
    porcelain = _git(["status", "--porcelain"], cwd=root)
    changed, untracked = _parse_status(porcelain)

    total = len(changed) + len(untracked)
    code_untracked = sorted(p for p in untracked if _is_code(p))
    code_changed = sorted(p for p in changed if _is_code(p))

    print("=== 工作区体检 ===")
    print(f"仓库根：{root}")
    print(f"已跟踪改动：{len(changed)} 个；未跟踪：{len(untracked)} 个；合计 {total} 个。")

    warnings: list[str] = []

    if total > MAX_CHANGED_FILES:
        warnings.append(
            f"工作区共有 {total} 个改动文件（阈值 {MAX_CHANGED_FILES}）。"
            "建议按模块小步提交，别让大块改动长期堆积。"
        )
    if code_untracked:
        warnings.append(
            f"有 {len(code_untracked)} 个未跟踪的代码/schema 文件尚未纳入版本控制："
        )
    if code_changed and len(changed) > MAX_CHANGED_FILES // 2:
        warnings.append(
            f"有 {len(code_changed)} 个已跟踪的代码/schema 文件被改但未提交。"
        )

    if code_untracked:
        print("\n未跟踪的代码/schema 文件：")
        for p in code_untracked:
            print(f"  ?? {p}")

    if not warnings:
        print("\n[OK] 工作区干净度可接受，无需特别关注。")
        return 0

    print("\n[!] 需要关注：")
    for w in warnings:
        print(f"  - {w}")
    print(
        "\n提醒（见 AGENTS.md 第 6 节 / decisions 约定 C）：\n"
        "  1) 按模块小步提交，避免一次性巨型 commit，便于 review 与回滚；\n"
        "  2) 改完即在 TASK_STATUS.md 看板登记角色 / 改动 / 文件 / 测试结果；\n"
        "  3) 涉及 schema 或聚合口径变化的，先在 decisions/ 留痕再落地。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
