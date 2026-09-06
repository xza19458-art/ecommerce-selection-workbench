"""Print the read-only deployment preflight report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.deployment_preflight import get_deployment_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查桌面应用部署环境")
    parser.add_argument("--root", type=Path, help="覆盖用户数据根目录，仅用于诊断")
    parser.add_argument(
        "--database-config",
        type=Path,
        help="覆盖 database.json 路径，仅用于诊断",
    )
    args = parser.parse_args()
    report = get_deployment_preflight(
        root=args.root,
        database_config_path=args.database_config,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready_for_analysis"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
