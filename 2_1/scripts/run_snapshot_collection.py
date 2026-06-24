from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.snapshot_collection_runner import run_snapshot_collection


def main() -> int:
    parser = argparse.ArgumentParser(
        description="手动触发低频 Amazon 搜索页快照采集。默认 dry-run，不联网。"
    )
    parser.add_argument("--run", action="store_true", help="真正联网采集；不加该参数只做 dry-run 预览")
    parser.add_argument("--max-keywords", type=int, default=3, help="单轮最多关键词，硬上限 3")
    parser.add_argument("--min-interval-hours", type=int, default=None, help="同关键词最短间隔；不传则读取 settings，硬下限 72")
    parser.add_argument("--pages-per-keyword", type=int, default=None, help="单关键词默认页数；不传则读取 settings，硬上限 7")
    parser.add_argument("--max-pages-per-keyword", type=int, default=None, help="单关键词页数上限；不传则读取 settings，硬上限 7")
    parser.add_argument("--target-snapshots", type=int, default=3, help="趋势可用目标快照数，默认 3")
    parser.add_argument("--marketplace", default=None, help="站点过滤，如 US")
    parser.add_argument("--keyword", default=None, help="关键词模糊过滤")
    parser.add_argument("--keyword-exact", action="store_true", help="关键词精确匹配，用于追踪任务调度")
    parser.add_argument("--save-root", default="html/snapshots", help="HTML 保存根目录")
    parser.add_argument("--stop-file", default="runtime/stop_snapshot_collection.flag", help="存在该文件时停止本轮")
    parser.add_argument("--page-delay-min", type=int, default=None, help="页间隔最小秒数；不传则读取 settings，硬下限 5")
    parser.add_argument("--page-delay-max", type=int, default=None, help="页间隔最大秒数；不传则读取 settings")
    parser.add_argument("--max-runtime-minutes", type=int, default=None, help="单轮最长分钟数；不传则读取 settings，仅校验正整数")
    parser.add_argument(
        "--manifest",
        default="数据结果/snapshot_collection_run_manifest.json",
        help="运行清单输出路径；设为空字符串则不写",
    )
    parser.add_argument("--no-job-log", action="store_true", help="真实采集时不写 crawl_jobs 任务日志")
    parser.add_argument("--ignore-interval", action="store_true", help="人工补采：豁免 72h 间隔（默认守 72h），仅人工显式使用")
    args = parser.parse_args()

    manifest = args.manifest or None
    summary = run_snapshot_collection(
        run=args.run,
        max_keywords=args.max_keywords,
        min_interval_hours=args.min_interval_hours,
        pages_per_keyword=args.pages_per_keyword,
        max_pages_per_keyword=args.max_pages_per_keyword,
        target_snapshots=args.target_snapshots,
        marketplace=args.marketplace,
        keyword=args.keyword,
        keyword_exact=args.keyword_exact,
        save_root=args.save_root,
        stop_file=args.stop_file,
        page_delay_min_seconds=args.page_delay_min,
        page_delay_max_seconds=args.page_delay_max,
        max_runtime_minutes=args.max_runtime_minutes,
        manifest_path=manifest,
        record_jobs=not args.no_job_log,
        ignore_interval=args.ignore_interval,
    )
    _print_summary(summary)
    return 0 if summary.status in {"完成", "dry-run"} else 1


def _print_summary(summary) -> None:
    print("低频快照联网采集器")
    print("=" * 32)
    print(f"模式: {'真实采集' if not summary.dry_run else 'dry-run 预览'}")
    print(f"状态: {summary.status}")
    print(f"采集批次时间: {summary.snapshot_at:%Y-%m-%d %H:%M:%S}")
    print(f"说明: {summary.message}")
    if summary.manifest_path:
        print(f"运行清单: {summary.manifest_path}")

    print("\n计划任务:")
    if not summary.plan.tasks:
        print("- 暂无到期关键词")
    for task in summary.plan.tasks:
        print(
            f"- [{task.marketplace}] {task.keyword} | 页数 {task.recommended_pages} | "
            f"平均快照 {task.avg_snapshots_per_product:.2f} | 原因: {task.reason}"
        )
        for page in task.pages:
            print(f"  P{page.page_no}: {page.url}")

    if not summary.pages:
        return

    print("\n采集结果:")
    for page in summary.pages:
        print(
            f"- [{page.status}] {page.keyword} P{page.page_no} | "
            f"解析 {page.total_found} / 有效 {page.total_valid} | {page.saved_file or '--'}"
        )
        if page.reason:
            print(f"  原因: {page.reason}")

    saved_files = [page.saved_file for page in summary.pages if page.status == "saved" and page.saved_file]
    if saved_files:
        print("\n后续入库示例:")
        quoted = " ".join(f'"{path}"' for path in saved_files)
        print(
            f'python scripts/import_snapshots_and_sync.py {quoted} '
            f'--snapshot-at "{summary.snapshot_at:%Y-%m-%d %H:%M:%S}"'
        )


if __name__ == "__main__":
    raise SystemExit(main())
