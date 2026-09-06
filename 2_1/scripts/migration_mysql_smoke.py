"""Disposable real-MySQL smoke test for fresh schema migration bootstrap."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.migration_runner import (  # noqa: E402
    MIGRATIONS_DIR,
    MigrationManager,
    get_database_readiness,
)
from database.mysql_client import (  # noqa: E402
    DatabaseConfig,
    MySQLClient,
    _quote_mysql_identifier,
)


DATABASE_PREFIX = "amazon_selection_migration_smoke_"


def main() -> int:
    base = DatabaseConfig.from_file()
    database = f"{DATABASE_PREFIX}{os.getpid()}_{int(time.time())}"
    if not re.fullmatch(r"amazon_selection_migration_smoke_[0-9_]+", database):
        raise RuntimeError("临时数据库名安全校验失败")
    client = MySQLClient(replace(base, database=database))
    try:
        first = client.initialize_schema()
        readiness = get_database_readiness(client)
        second = client.initialize_schema()
        if first["action"] != "fresh_baseline":
            raise RuntimeError(f"首次初始化动作异常: {first['action']}")
        if not readiness["ready"]:
            raise RuntimeError(readiness["message"])
        if second["action"] != "current":
            raise RuntimeError(f"重复初始化动作异常: {second['action']}")
        with tempfile.TemporaryDirectory(prefix="amazon_selection_migrations_") as temp:
            test_migrations = Path(temp)
            for path in MIGRATIONS_DIR.glob("*.sql"):
                shutil.copy2(path, test_migrations / path.name)
            forward = test_migrations / "20990101_smoke_probe.sql"
            rollback = test_migrations / "20990101_smoke_probe.down.sql"
            forward.write_text(
                "CREATE TABLE IF NOT EXISTS migration_smoke_probe (id INT NOT NULL PRIMARY KEY);\n",
                encoding="utf-8",
            )
            rollback.write_text("DROP TABLE IF EXISTS migration_smoke_probe;\n", encoding="utf-8")
            manager = MigrationManager(client, migrations_dir=test_migrations)
            migrated = manager.initialize(
                schema_path=ROOT / "database" / "schema.sql",
                legacy_bootstrap=client._bootstrap_legacy_schema,
            )
            migrated_readiness = get_database_readiness(client, migrations_dir=test_migrations)
            rolled_back = manager.rollback("20990101_smoke_probe", confirmed=True)
            rolled_back_readiness = get_database_readiness(client, migrations_dir=test_migrations)
            reapplied = manager.initialize(
                schema_path=ROOT / "database" / "schema.sql",
                legacy_bootstrap=client._bootstrap_legacy_schema,
            )
            if migrated["action"] != "migrated" or not migrated_readiness["ready"]:
                raise RuntimeError("增量迁移未正确执行")
            if rolled_back["status"] != "rolled_back" or rolled_back_readiness["ready"]:
                raise RuntimeError("回滚后迁移状态未正确标记为待应用")
            if reapplied["action"] != "migrated":
                raise RuntimeError("回滚后的迁移未能重新应用")
        print(
            "MIGRATION_MYSQL_SMOKE_OK "
            f"database={database} baseline={readiness['migration_count']} incremental=1 rollback=1 "
            f"business_tables={readiness['business_table_count']}"
        )
        return 0
    finally:
        if not database.startswith(DATABASE_PREFIX):
            raise RuntimeError("拒绝删除非迁移冒烟数据库")
        with client.connect(database="") as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS {_quote_mysql_identifier(database)}")


if __name__ == "__main__":
    raise SystemExit(main())
