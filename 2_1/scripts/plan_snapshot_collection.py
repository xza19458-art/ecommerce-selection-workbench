from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.snapshot_collection_plan import SnapshotCollectionPlan, build_snapshot_collection_plan
from services.settings import get_collection_limits


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成低频快照采集 dry-run 计划。只读 MySQL，不联网、不写库。"
    )
    parser.add_argument("--max-keywords", type=int, default=3, help="本轮最多采集关键词数，默认 3")
    parser.add_argument("--min-interval-hours", type=int, default=None, help="同关键词最短采集间隔小时；不传则读取 settings，硬下限 72")
    parser.add_argument("--pages-per-keyword", type=int, default=None, help="单关键词默认页数；不传则读取 settings")
    parser.add_argument("--max-pages-per-keyword", type=int, default=None, help="单关键词页数上限；不传则读取 settings，硬上限 7")
    parser.add_argument("--target-snapshots", type=int, default=3, help="趋势可用目标快照数，默认 3")
    parser.add_argument("--marketplace", default=None, help="站点过滤，如 US")
    parser.add_argument("--keyword", default=None, help="关键词模糊过滤")
    parser.add_argument("--save-root", default="html/snapshots", help="建议 HTML 保存根目录")
    parser.add_argument("--output", default=None, help="导出文件路径，支持 .csv / .json")
    parser.add_argument("--include-pages", action="store_true", help="导出时展开到页级 URL")
    args = parser.parse_args()

    limits = get_collection_limits()
    max_pages_per_keyword = _clamp_int(
        args.max_pages_per_keyword,
        default=limits.max_pages_per_keyword,
        minimum=1,
        maximum=limits.max_pages_per_keyword,
    )
    pages_per_keyword = _clamp_int(
        args.pages_per_keyword,
        default=limits.pages_per_keyword,
        minimum=1,
        maximum=max_pages_per_keyword,
    )

    plan = build_snapshot_collection_plan(
        max_keywords=args.max_keywords,
        min_interval_hours=max(limits.tracking_min_interval_hours, args.min_interval_hours or limits.tracking_min_interval_hours),
        default_pages=pages_per_keyword,
        max_pages_per_keyword=max_pages_per_keyword,
        target_snapshots=args.target_snapshots,
        marketplace=args.marketplace,
        keyword=args.keyword,
        save_root=args.save_root,
    )

    _print_plan(plan)
    if args.output:
        _export_plan(plan, args.output, include_pages=args.include_pages)
        print(f"\n已导出: {args.output}")
    return 0


def _clamp_int(value: int | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = default if value is None else int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _print_plan(plan: SnapshotCollectionPlan) -> None:
    summary = plan.summary()
    print("低频快照采集计划 dry-run")
    print("=" * 32)
    for key, value in summary.items():
        print(f"{key}: {value}")

    if not plan.tasks:
        print("\n暂无到期关键词。可降低 --min-interval-hours 或检查关键词/快照数据。")
        return

    print("\n任务列表:")
    for task in plan.tasks:
        last_time = task.last_collected_at.strftime("%Y-%m-%d %H:%M:%S") if task.last_collected_at else "--"
        hours = f"{task.hours_since_last:.1f}" if task.hours_since_last is not None else "--"
        score = f"{task.max_score:.2f}" if task.max_score is not None else "--"
        print(
            f"{task.priority}. [{task.marketplace}] {task.keyword} | "
            f"商品 {task.tracked_products} | 平均快照 {task.avg_snapshots_per_product:.2f} | "
            f"最高分 {score} | 上次 {last_time} | 间隔 {hours}h | 页数 {task.recommended_pages}"
        )
        print(f"   原因: {task.reason}")
        print(f"   保存目录: {task.save_dir}")
        for page in task.pages:
            print(f"   P{page.page_no}: {page.url}")


def _export_plan(plan: SnapshotCollectionPlan, output: str, *, include_pages: bool) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    rows = plan.to_rows(include_pages=include_pages)

    if suffix == ".json":
        payload: dict[str, Any] = {
            "summary": plan.summary(),
            "tasks": [
                {
                    **task.to_dict(),
                    "页面": [page.to_dict() for page in task.pages],
                }
                for task in plan.tasks
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    if suffix != ".csv":
        raise SystemExit("导出路径仅支持 .csv 或 .json")
    if not rows:
        rows = [plan.summary()]
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
