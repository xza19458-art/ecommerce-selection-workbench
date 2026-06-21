from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.review_html_export import export_review_html_files


def main() -> None:
    parser = argparse.ArgumentParser(description="解析本地 Amazon 评论页 HTML，导出可导入的 CSV/JSON。")
    parser.add_argument("files", nargs="+", help="本地评论页 HTML 文件路径，可一次传多个")
    parser.add_argument("--asin", default=None, help="当 HTML 中无法识别 ASIN 时使用的默认 ASIN")
    parser.add_argument("--format", choices=("csv", "json"), default="csv", help="输出格式，默认 csv")
    parser.add_argument("--output", default=None, help="输出 CSV/JSON 路径，默认写入 数据结果/评论HTML解析.csv")
    parser.add_argument("--rejected-output", default=None, help="过滤评论明细 CSV 路径")
    args = parser.parse_args()

    summary = export_review_html_files(
        args.files,
        output_path=args.output,
        output_format=args.format,
        default_asin=args.asin,
        rejected_output_path=args.rejected_output,
    )
    print("评论页 HTML 解析完成")
    print(f"解析评论数: {summary.total_found}")
    print(f"有效评论数: {summary.total_valid}")
    print(f"过滤评论数: {summary.total_rejected}")
    print(f"输出文件: {summary.output_path}")
    print(f"过滤明细: {summary.rejected_output_path}")
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
