from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.recommendations import export_recommendations_csv, fetch_top_recommendations, to_chinese_row


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 MySQL 中的潜力商品推荐榜单。")
    parser.add_argument("--limit", type=int, default=50, help="导出数量")
    parser.add_argument("--output", default="数据结果", help="输出目录")
    args = parser.parse_args()

    rows = fetch_top_recommendations(limit=args.limit)
    path = export_recommendations_csv(args.output, limit=args.limit)
    print(f"推荐商品数: {len(rows)}")
    print(f"推荐榜单: {path}")
    if rows:
        top = to_chinese_row(rows[0])
        print("最高分商品:")
        for key in ["ASIN", "商品标题", "综合得分", "价格", "评分", "评论数", "近月购买量", "推荐理由"]:
            print(f"{key}: {top.get(key)}")


if __name__ == "__main__":
    main()
