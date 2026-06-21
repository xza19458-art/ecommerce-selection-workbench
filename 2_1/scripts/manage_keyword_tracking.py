from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.keyword_tracking import (
    count_keyword_snapshot_times,
    create_tracking_task,
    delete_tracking_task,
    ensure_keyword_tracking_schema,
    list_tracking_tasks,
    refresh_all_tracking_task_progress,
    refresh_tracking_task_progress,
    update_tracking_task_status,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="关键词追踪任务表管理工具。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="创建 keyword_tracking_tasks 表")

    add_parser = subparsers.add_parser("add", help="创建关键词追踪任务")
    add_parser.add_argument("keyword", help="关键词")
    add_parser.add_argument("--marketplace", default="US", help="站点，默认 US")
    add_parser.add_argument("--target-snapshots", type=int, default=3, help="目标快照时间点数，默认 3")
    add_parser.add_argument("--pages-per-keyword", type=int, default=2, help="每轮采集页数，默认 2")

    list_parser = subparsers.add_parser("list", help="列出追踪任务")
    list_parser.add_argument("--status", default=None, help="状态过滤")
    list_parser.add_argument("--marketplace", default=None, help="站点过滤")
    list_parser.add_argument("--keyword", default=None, help="关键词模糊过滤")
    list_parser.add_argument("--limit", type=int, default=200, help="最大返回条数")
    list_parser.add_argument("--json", action="store_true", help="输出 JSON")

    count_parser = subparsers.add_parser("count", help="查询关键词当前快照时间点数")
    count_parser.add_argument("keyword", help="关键词")
    count_parser.add_argument("--marketplace", default="US", help="站点，默认 US")

    refresh_parser = subparsers.add_parser("refresh", help="刷新任务达标状态")
    refresh_parser.add_argument("--id", type=int, default=None, help="任务 ID；不传则刷新 active 任务")

    status_parser = subparsers.add_parser("status", help="修改任务状态")
    status_parser.add_argument("id", type=int, help="任务 ID")
    status_parser.add_argument("status", help="active/completed/paused/error")
    status_parser.add_argument("--error-message", default=None, help="error 状态说明")

    delete_parser = subparsers.add_parser("delete", help="删除任务")
    delete_parser.add_argument("id", type=int, help="任务 ID")

    args = parser.parse_args()

    if args.command == "init":
        ensure_keyword_tracking_schema()
        print("keyword_tracking_tasks 表已就绪")
        return 0
    if args.command == "add":
        task = create_tracking_task(
            marketplace=args.marketplace,
            keyword=args.keyword,
            target_snapshots=args.target_snapshots,
            pages_per_keyword=args.pages_per_keyword,
        )
        _print_tasks([task])
        return 0
    if args.command == "list":
        tasks = list_tracking_tasks(
            status=args.status,
            marketplace=args.marketplace,
            keyword=args.keyword,
            limit=args.limit,
        )
        if args.json:
            print(json.dumps([task.to_dict() for task in tasks], ensure_ascii=False, indent=2))
        else:
            _print_tasks(tasks)
        return 0
    if args.command == "count":
        count = count_keyword_snapshot_times(marketplace=args.marketplace, keyword=args.keyword)
        print(count)
        return 0
    if args.command == "refresh":
        if args.id is not None:
            _print_tasks([refresh_tracking_task_progress(args.id)])
        else:
            _print_tasks(refresh_all_tracking_task_progress())
        return 0
    if args.command == "status":
        task = update_tracking_task_status(args.id, args.status, error_message=args.error_message)
        _print_tasks([task])
        return 0
    if args.command == "delete":
        deleted = delete_tracking_task(args.id)
        print("已删除" if deleted else "未找到任务")
        return 0
    raise SystemExit(f"未知命令: {args.command}")


def _print_tasks(tasks) -> None:
    if not tasks:
        print("暂无关键词追踪任务")
        return
    for task in tasks:
        row = task.to_dict()
        print(
            f"#{row['id']} [{row['marketplace']}] {row['keyword']} | "
            f"状态 {row['status']} | 快照 {row['current_snapshots']}/{row['target_snapshots']} | "
            f"页数 {row['pages_per_keyword']} | 上次采集 {row['last_collected_at'] or '--'}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
