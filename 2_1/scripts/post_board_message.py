"""安全地向 TASK_STATUS.md 的「📨 留言区」追加一条留言。

由 Codex-i18n 提议、Claude-Lead 裁决采纳（见看板改动记录）。目的：避免各对话
在 Windows 下手工/patch 直接编辑含 emoji、中文、箭头的 Markdown 行时，出现 GBK
编码乱码、不可见字符、插入位置错乱等问题。本脚本统一以 UTF-8 / LF 读写，固定把
留言追加到留言区末尾，并在写后校验。

用法：
    python 2_1/scripts/post_board_message.py \
        --from Codex-i18n --to 全体 --status 待处理 "留言内容"

留言会按统一格式写入（见 AGENTS.md §3.3）：
    - 自身名 → 对象名：内容 —— 状态

退出码：成功 0；找不到留言区或写后校验失败 1。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

BOARD_HEADING = "📨 留言区"
VALID_STATUS = ("待处理", "处理中", "已回复", "待执行", "待各对话确认")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(here.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if top.returncode == 0 and top.stdout.strip():
        return Path(top.stdout.strip())
    # 回退：根据已知结构推导（scripts -> 2_1 -> 仓库根）
    return here.parents[2]


def _find_board_bounds(lines: list[str]) -> tuple[int, int]:
    """返回 (留言区标题行下标, 留言区结束下标)。结束为下一个 `## ` 或 `---`。"""
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("## ") and BOARD_HEADING in ln),
        None,
    )
    if start is None:
        raise SystemExit(f"未找到「{BOARD_HEADING}」标题，请确认 TASK_STATUS.md 结构。")
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ") or lines[j].strip() == "---":
            end = j
            break
    return start, end


def main() -> int:
    parser = argparse.ArgumentParser(description="向 TASK_STATUS.md 留言区追加一条留言")
    parser.add_argument("--from", dest="sender", required=True, help="自身名，如 Codex-i18n")
    parser.add_argument("--to", dest="receiver", required=True, help="沟通对象名，如 全体 / Claude-Lead")
    parser.add_argument("--status", default="待处理", help=f"状态，建议取值：{', '.join(VALID_STATUS)}")
    parser.add_argument("content", help="留言内容（一行，勿含换行）")
    args = parser.parse_args()

    if "\n" in args.content:
        raise SystemExit("留言内容必须是一行，请勿包含换行。")

    root = _repo_root()
    path = root / "TASK_STATUS.md"
    if not path.exists():
        raise SystemExit(f"未找到看板文件：{path}")

    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    start, end = _find_board_bounds(lines)

    message = f"- {args.sender} → {args.receiver}：{args.content} —— {args.status}"

    # 插到留言区内容末尾（跳过尾部空行），保持与下方分隔之间留一空行。
    insert_at = end
    while insert_at - 1 > start and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    lines.insert(insert_at, message)

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    # 写后校验
    if message not in path.read_text(encoding="utf-8"):
        print("[!] 写入后未能在文件中校验到该留言，请人工检查。")
        return 1

    print("[OK] 已写入留言区：")
    print("  " + message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
