from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.snapshot_storage import ingest_snapshot_html_and_sync_warehouse


def main() -> int:
    parser = argparse.ArgumentParser(
        description="导入低频补采 HTML 快照到 MySQL，并同步 DuckDB/Parquet 分析仓库。"
    )
    parser.add_argument("html", nargs="+", help="HTML 文件路径")
    parser.add_argument("--keyword", default=None, help="关键词")
    parser.add_argument("--marketplace", default="US", help="站点，默认 US")
    parser.add_argument("--snapshot-at", default=None, help="采集时间，如 2026-06-17 09:00:00；默认导入当小时")
    parser.add_argument("--url", default=None, help="采集来源 URL")
    parser.add_argument("--pages", type=int, default=None, help="采集页数")
    parser.add_argument("--allow-incomplete", action="store_true", help="允许缺少近月购买量等字段")
    args = parser.parse_args()

    snapshot_at = _parse_snapshot_at(args.snapshot_at)
    summary = ingest_snapshot_html_and_sync_warehouse(
        args.html,
        keyword=args.keyword,
        marketplace=args.marketplace,
        snapshot_at=snapshot_at,
        url=args.url,
        pages=args.pages,
        require_complete=not args.allow_incomplete,
    )
    _print_summary(summary.to_dict())
    return 0


def _parse_snapshot_at(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit("--snapshot-at 格式无效，请使用 YYYY-MM-DD HH:MM:SS") from exc


def _print_summary(summary: dict) -> None:
    mysql = summary["mysql"]
    warehouse = summary["warehouse"]
    print("新快照入库 + 分析仓库同步完成")
    print("=" * 40)
    print(f"采集时间: {summary.get('snapshot_at') or '导入当小时'}")
    print(f"MySQL 解析商品数: {mysql['total_found']}")
    print(f"MySQL 有效商品数: {mysql['total_valid']}")
    print(f"MySQL 过滤商品数: {mysql['total_rejected']}")
    print(f"MySQL 入库/更新商品数: {mysql['total_inserted']}")
    if mysql["rejected_reasons"]:
        print("过滤原因:")
        for reason, count in mysql["rejected_reasons"].items():
            print(f"- {reason}: {count}")
    print(f"DuckDB: {warehouse['duckdb_path']}")
    print(f"Parquet: {warehouse['parquet_dir']}")
    print(f"仓库同步总行数: {warehouse['total_rows']}")
    for name, rows in warehouse["tables"].items():
        print(f"- {name}: {rows}")


if __name__ == "__main__":
    raise SystemExit(main())
