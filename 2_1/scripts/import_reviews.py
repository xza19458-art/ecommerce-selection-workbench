from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.review_import import import_reviews_from_file


def main() -> None:
    parser = argparse.ArgumentParser(description="导入本地 Amazon 评论 CSV/JSON，并生成评论痛点摘要。")
    parser.add_argument("file", help="评论 CSV 或 JSON 文件路径")
    parser.add_argument("--asin", default=None, help="当文件内没有 asin 字段时使用的默认 ASIN")
    args = parser.parse_args()

    summary = import_reviews_from_file(args.file, default_asin=args.asin)
    print(f"解析评论数: {summary.total_found}")
    print(f"有效评论数: {summary.total_valid}")
    print(f"过滤评论数: {summary.total_rejected}")
    print(f"写入/更新评论数: {summary.total_upserted}")
    print(f"生成洞察商品数: {summary.insights_generated}")
    if summary.involved_asins:
        print("涉及 ASIN:")
        for asin in sorted(summary.involved_asins):
            print(f"- {asin}")
    if summary.rejected_reasons:
        print("过滤原因:")
        for reason, count in summary.rejected_reasons.items():
            print(f"- {reason}: {count}")


if __name__ == "__main__":
    main()
