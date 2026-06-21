from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.ingestion import count_rejected_reasons, export_preview, parse_html_files


def main() -> None:
    parser = argparse.ArgumentParser(description="预览 Amazon HTML 解析质量，不写入数据库。")
    parser.add_argument("html", nargs="+", help="HTML 文件路径")
    parser.add_argument("--keyword", default=None, help="关键词")
    parser.add_argument("--output", default="数据结果", help="预览 CSV 输出目录")
    parser.add_argument("--allow-incomplete", action="store_true", help="允许缺少近月购买量等字段")
    args = parser.parse_args()

    valid, rejected = parse_html_files(
        args.html,
        keyword=args.keyword,
        require_complete=not args.allow_incomplete,
    )
    valid_path, rejected_path = export_preview(valid, rejected, args.output)

    print(f"解析商品数: {len(valid) + len(rejected)}")
    print(f"有效入库候选: {len(valid)}")
    print(f"过滤商品数: {len(rejected)}")
    print(f"有效数据预览: {valid_path}")
    print(f"过滤数据预览: {rejected_path}")
    reasons = count_rejected_reasons(rejected)
    if reasons:
        print("过滤原因:")
        for reason, count in reasons.items():
            print(f"- {reason}: {count}")


if __name__ == "__main__":
    main()
