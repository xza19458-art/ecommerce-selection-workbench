"""Build or verify a clean user-facing distribution from the tested onedir build."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.distribution_package import (
    build_clean_distribution,
    default_distribution_path,
    verify_clean_distribution,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建不含本机数据的干净桌面分发包")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("dist/AmazonSelectionWorkbench"),
        help="已通过发布门禁的 PyInstaller onedir 目录",
    )
    parser.add_argument("--output", type=Path, help="输出目录；默认写入 release/")
    parser.add_argument(
        "--guide",
        type=Path,
        default=Path("DEPLOYMENT_GUIDE.md"),
        help="随包部署与迁移说明",
    )
    parser.add_argument("--no-zip", action="store_true", help="不额外生成 ZIP")
    parser.add_argument("--verify-only", type=Path, help="只校验已有干净分发目录")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.verify_only:
        result = verify_clean_distribution(args.verify_only)
    else:
        output = args.output or default_distribution_path()
        result = build_clean_distribution(
            source_dir=args.source,
            output_dir=output,
            guide_path=args.guide,
            create_zip=not args.no_zip,
        ).to_dict()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
