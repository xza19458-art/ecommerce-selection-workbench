from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ingestion import ingest_html_files_to_mysql


def main() -> None:
    parser = argparse.ArgumentParser(description="解析 Amazon HTML 并写入 MySQL。")
    parser.add_argument("html", nargs="+", help="HTML 文件路径")
    parser.add_argument("--keyword", default=None, help="关键词")
    parser.add_argument("--snapshot-at", default=None, help="采集时间，如 2026-06-17 09:00:00；默认导入当小时")
    parser.add_argument("--url", default=None, help="采集来源 URL")
    parser.add_argument("--pages", type=int, default=None, help="采集页数")
    parser.add_argument("--allow-incomplete", action="store_true", help="允许缺少近月购买量等字段")
    args = parser.parse_args()

    summary = ingest_html_files_to_mysql(
        args.html,
        keyword=args.keyword,
        snapshot_at=_parse_snapshot_at(args.snapshot_at),
        url=args.url,
        pages=args.pages,
        require_complete=not args.allow_incomplete,
    )
    print(f"解析商品数: {summary.total_found}")
    print(f"有效商品数: {summary.total_valid}")
    print(f"过滤商品数: {summary.total_rejected}")
    print(f"入库商品数: {summary.total_inserted}")
    if summary.rejected_reasons:
        print("过滤原因:")
        for reason, count in summary.rejected_reasons.items():
            print(f"- {reason}: {count}")


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


if __name__ == "__main__":
    main()
