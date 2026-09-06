"""Inspect, apply, or explicitly roll back MySQL schema migrations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.migration_runner import (  # noqa: E402
    MigrationManager,
    SchemaMigrationError,
    get_database_readiness,
)
from database.mysql_client import MySQLClient  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理 Amazon 选品助手 MySQL 迁移")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--status", action="store_true", help="只读检查迁移与数据库就绪状态（默认动作）")
    action.add_argument("--apply", action="store_true", help="初始化或应用待执行迁移")
    action.add_argument("--rollback", metavar="MIGRATION_ID", help="回滚指定的最近增量迁移")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认执行回滚；基线迁移和没有 .down.sql 的迁移仍会被拒绝",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        client = MySQLClient()
        if args.apply:
            result = client.initialize_schema()
        elif args.rollback:
            result = MigrationManager(client).rollback(args.rollback, confirmed=args.confirm)
        else:
            result = get_database_readiness(client)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"状态: {result.get('state') or result.get('status') or result.get('action')}")
            print(result.get("message") or json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result.get("ready", True) else 1
    except SchemaMigrationError as exc:
        print(f"迁移失败: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI should return a concise failure.
        print(f"数据库操作失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
