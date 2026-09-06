from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.mysql_client import MySQLClient


def main() -> None:
    client = MySQLClient()
    result = client.initialize_schema()
    action_labels = {
        "fresh_baseline": "新库已按当前快照创建并建立迁移基线",
        "legacy_baseline": "旧库已核验并纳入迁移基线",
        "migrated": "待执行迁移已完成",
        "current": "数据库已经是当前版本",
    }
    print(action_labels.get(result["action"], result["action"]))
    print(
        f"数据库: {result['database']}；迁移文件: {result['migration_count']}；"
        f"基线: {result['baseline_count']}；增量应用: {result['applied_count']}"
    )


if __name__ == "__main__":
    main()
