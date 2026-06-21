from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.mysql_client import MySQLClient


def main() -> None:
    client = MySQLClient()
    client.initialize_schema()
    print("MySQL 数据库结构初始化完成。")


if __name__ == "__main__":
    main()
