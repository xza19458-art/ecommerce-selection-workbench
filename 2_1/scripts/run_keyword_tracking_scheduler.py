from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.keyword_tracking_scheduler import run_keyword_tracking_scheduler


def main() -> int:
    parser = argparse.ArgumentParser(
        description="关键词追踪调度器：检查 active 任务，到期后可串行采集并入库同步。默认不联网。"
    )
    parser.add_argument("--execute", action="store_true", help="执行到期任务；不加则只检查/排队预览")
    parser.add_argument("--task-id", type=int, default=None, help="只检查/执行指定任务 ID")
    parser.add_argument("--limit", type=int, default=20, help="最多检查 active 任务数")
    parser.add_argument("--min-interval-hours", type=int, default=72, help="同任务最短采集间隔，默认 72")
    parser.add_argument("--save-root", default="html/tracking_snapshots", help="B1 保存 HTML 根目录")
    parser.add_argument("--stop-file", default="runtime/stop_keyword_tracking.flag", help="存在该文件时停止本轮")
    parser.add_argument("--manifest-root", default="数据结果/keyword_tracking_runs", help="B1 运行清单目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    summary = run_keyword_tracking_scheduler(
        execute=args.execute,
        task_id=args.task_id,
        limit=args.limit,
        min_interval_hours=args.min_interval_hours,
        save_root=args.save_root,
        stop_file=args.stop_file,
        manifest_root=args.manifest_root,
    )
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_summary(summary)
    return 0 if summary.status == "完成" else 1


def _print_summary(summary) -> None:
    print("关键词追踪调度器")
    print("=" * 32)
    print(f"模式: {'执行采集' if summary.executed else '检查预览'}")
    print(f"状态: {summary.status}")
    print(f"说明: {summary.message}")
    if not summary.decisions:
        print("暂无任务")
        return
    for decision in summary.decisions:
        row = decision.to_dict()
        print(
            f"- #{row['任务ID']} [{row['站点']}] {row['关键词']} | "
            f"{row['当前快照数']}/{row['目标快照数']} | {row['动作']} | {row['原因']}"
        )
        if row["采集状态"]:
            print(f"  采集状态: {row['采集状态']}")
        if row["保存HTML"]:
            print("  保存HTML:")
            for path in row["保存HTML"]:
                print(f"  - {path}")
        if row["入库商品数"] is not None:
            print(f"  入库商品数: {row['入库商品数']}")


if __name__ == "__main__":
    raise SystemExit(main())
