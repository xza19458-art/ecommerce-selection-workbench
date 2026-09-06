from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.migration_runner import (  # noqa: E402
    MigrationManager,
    checksum_issues,
    discover_migrations,
    inspect_required_schema,
    split_sql,
)


def test_discover_migrations_is_sorted_and_pairs_down_file(tmp_path: Path) -> None:
    (tmp_path / "20260202_second.sql").write_text("CREATE TABLE second (id INT);", encoding="utf-8")
    (tmp_path / "20260101_first.sql").write_text("CREATE TABLE first (id INT);", encoding="utf-8")
    (tmp_path / "20260101_first.down.sql").write_text("DROP TABLE first;", encoding="utf-8")

    migrations = discover_migrations(tmp_path)

    assert [item.migration_id for item in migrations] == ["20260101_first", "20260202_second"]
    assert migrations[0].rollback_path == tmp_path / "20260101_first.down.sql"
    assert migrations[0].rollback_checksum
    assert migrations[1].rollback_path is None


def test_checksum_issues_detects_changed_and_removed_successful_files(tmp_path: Path) -> None:
    path = tmp_path / "20260101_first.sql"
    path.write_text("SELECT 1;", encoding="utf-8")
    migration = discover_migrations(tmp_path)[0]
    records = {
        migration.migration_id: {"status": "applied", "checksum": "bad"},
        "20250101_removed": {"status": "applied", "checksum": "old"},
        "20250102_failed": {"status": "failed", "checksum": "old"},
    }

    changed, orphaned = checksum_issues(records, [migration])

    assert changed == [migration.migration_id]
    assert orphaned == ["20250101_removed"]


def test_split_sql_ignores_comments_and_keeps_multiline_statements() -> None:
    statements = split_sql(
        """
        -- comment
        USE amazon_selection;

        CREATE TABLE example (
          id INT
        );
        """
    )

    assert statements == ["USE amazon_selection", "CREATE TABLE example (\n          id INT\n        )"]


class _InventoryCursor:
    def execute(self, _sql, _params):
        return None

    def fetchall(self):
        return [
            {"table_name": "products", "column_name": "id"},
            {"table_name": "products", "column_name": "marketplace"},
            {"table_name": "products", "column_name": "asin"},
            {"table_name": "products", "column_name": "title"},
        ]


def test_required_schema_inventory_reports_missing_tables_and_columns() -> None:
    missing_tables, missing_columns = inspect_required_schema(_InventoryCursor(), "example")

    assert "product_snapshots" in missing_tables
    assert "products" not in missing_tables
    assert "title_zh" in missing_columns["products"]


def test_execute_file_never_uses_hardcoded_database(tmp_path: Path) -> None:
    path = tmp_path / "migration.sql"
    path.write_text(
        "CREATE DATABASE old_name;\nUSE old_name;\nCREATE TABLE retained (id INT);\n",
        encoding="utf-8",
    )

    class Cursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement):
            self.statements.append(statement)

    cursor = Cursor()
    MigrationManager._execute_file(cursor, path)

    assert cursor.statements == ["CREATE TABLE retained (id INT)"]


def test_business_services_do_not_execute_runtime_schema_ensures() -> None:
    offenders = []
    for path in sorted((ROOT / "services").glob("*.py")):
        if "db.ensure_" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)

    assert offenders == []
