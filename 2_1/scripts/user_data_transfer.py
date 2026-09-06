"""Back up, inspect, or restore the local evidence archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.user_data_transfer import (
    create_user_data_backup,
    inspect_user_data_archive,
    restore_user_data_backup,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="迁移本地 HTML、导出、设置与分析仓库；不迁移 MySQL"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="生成带 SHA-256 清单的迁移包")
    backup.add_argument("--source-root", type=Path)
    backup.add_argument("--output", type=Path)
    backup.add_argument(
        "--include-private-config",
        action="store_true",
        help="显式加入数据库/Agent 等明文私有配置",
    )

    inspect = subparsers.add_parser("inspect", help="完整校验并查看迁移包")
    inspect.add_argument("archive", type=Path)

    restore = subparsers.add_parser("restore", help="完整校验后恢复本地文件")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--target-root", type=Path)
    restore.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖不同内容，并先将原文件备份到 migration_restore_backups/",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "backup":
        result = create_user_data_backup(
            source_root=args.source_root,
            output_path=args.output,
            include_private_config=args.include_private_config,
        ).to_dict()
    elif args.command == "inspect":
        result = inspect_user_data_archive(args.archive)
    else:
        result = restore_user_data_backup(
            args.archive,
            target_root=args.target_root,
            overwrite=args.overwrite,
        ).to_dict()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
