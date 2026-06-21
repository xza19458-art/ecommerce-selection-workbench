from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.snapshot_storage import evaluate_snapshot_storage


def main() -> int:
    parser = argparse.ArgumentParser(description="只读评估快照表容量、索引和一年增长量级。")
    parser.add_argument("--interval-days", type=float, default=3.0, help="预计采集间隔天数，默认 3 天")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    report = evaluate_snapshot_storage(collection_interval_days=args.interval_days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0


def _print_report(report: dict) -> None:
    print("快照存储容量/索引评估")
    print("=" * 36)
    print(f"预计采集间隔: {report['collection_interval_days']} 天")

    print("\n表容量:")
    for table, info in report["tables"].items():
        print(
            f"- {table}: 估算行数 {info['estimated_rows']} | "
            f"数据 {info['data_bytes']} bytes | 索引 {info['index_bytes']} bytes | 总计 {info['total_mb']} MB"
        )

    print("\n实际统计:")
    for table, stats in report["stats"].items():
        print(f"- {table}")
        for key, value in stats.items():
            print(f"  {key}: {value}")

    print("\n一年增长估算:")
    for key, value in report["projection"].items():
        print(f"- {key}: {value}")

    print("\n索引:")
    for table, indexes in report["indexes"].items():
        print(f"- {table}")
        for name, info in indexes.items():
            unique = "UNIQUE" if info["unique"] else "INDEX"
            print(f"  {name}: {unique}({', '.join(info['columns'])})")

    print("\n建议:")
    for item in report["recommendations"]:
        print(f"- {item}")


if __name__ == "__main__":
    raise SystemExit(main())
