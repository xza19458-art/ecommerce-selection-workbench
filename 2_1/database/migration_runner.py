"""MySQL schema migration ledger, execution, and readiness checks.

Historical SQL files predate the ledger and are not all replay-safe. A fresh
database is therefore created from ``schema.sql`` and an existing unmanaged
database is brought to the same additive snapshot before those files are
recorded as a baseline. Only files added after that baseline are executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import time
from typing import Any, Callable, Iterable

from pkg_paths import resource_path


MIGRATIONS_DIR = resource_path("database", "migrations")
SUCCESS_STATUS = "applied"

SCHEMA_MIGRATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  migration_id VARCHAR(190) NOT NULL COMMENT 'Migration identifier derived from filename',
  filename VARCHAR(255) NOT NULL COMMENT 'Forward migration filename',
  checksum CHAR(64) NOT NULL COMMENT 'SHA256 of forward SQL file',
  status VARCHAR(16) NOT NULL COMMENT 'running/applied/failed/rolled_back',
  execution_mode VARCHAR(16) NOT NULL COMMENT 'baseline/migration/rollback',
  rollback_checksum CHAR(64) NULL COMMENT 'SHA256 of companion down SQL when present',
  execution_ms INT UNSIGNED NULL COMMENT 'Latest execution duration in milliseconds',
  error_message TEXT NULL COMMENT 'Latest migration failure',
  applied_at DATETIME NULL COMMENT 'Latest successful apply time',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (migration_id),
  KEY idx_schema_migrations_status (status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Application schema migration ledger'
"""

# These are the business structures required by the current release. Columns
# focus on cross-module contracts and fields introduced by historical upgrades.
REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "products": (
        "id",
        "marketplace",
        "asin",
        "title",
        "title_zh",
        "product_size",
        "date_first_available",
        "detail_collected_at",
        "detail_source_file",
    ),
    "product_snapshots": (
        "id",
        "product_id",
        "snapshot_at",
        "price",
        "rating",
        "review_count",
        "monthly_bought",
    ),
    "product_bsr_snapshots": ("product_id", "snapshot_at", "rank_value", "category_name"),
    "product_physical_specs": ("product_id", "item_length_in", "package_weight_oz"),
    "product_offer_snapshots": ("product_id", "snapshot_at", "current_price"),
    "product_variants": ("marketplace", "parent_asin", "child_asin"),
    "product_metric_inputs": ("product_id", "period_start", "period_end", "sessions"),
    "product_estimates": ("product_id", "as_of_date", "metric_key", "model_version"),
    "product_reviews": (
        "product_id",
        "content_hash",
        "title_zh",
        "body_zh",
        "review_translation_status",
    ),
    "product_review_insights": ("product_id", "insight_date", "pain_points_json"),
    "translation_cache": ("source_hash", "source_lang", "target_lang", "engine"),
    "keywords": ("marketplace", "keyword"),
    "keyword_serp_snapshots": ("keyword_id", "snapshot_at", "ad_density", "data_coverage"),
    "keyword_rank_snapshots": (
        "keyword_id",
        "product_id",
        "snapshot_at",
        "page_no",
        "organic_rank",
        "is_sponsored",
    ),
    "product_scores": ("product_id", "keyword_id", "score_date", "total_score"),
    "keyword_tracking_tasks": ("marketplace", "keyword", "status", "active_keyword"),
    "keyword_idea_runs": ("marketplace", "seed_keywords_json", "status"),
    "keyword_ideas": ("marketplace", "normalized_keyword", "status", "idea_score"),
    "crawl_jobs": ("status", "started_at", "total_inserted"),
    "research_projects": (
        "marketplace",
        "normalized_name",
        "status",
        "current_decision_report_version_id",
    ),
    "research_project_report_versions": (
        "project_id",
        "version_no",
        "freeze_kind",
        "decision_status",
        "evaluated_on",
        "method_version",
        "report_fingerprint",
        "report_json",
        "report_markdown",
        "idempotency_key",
        "frozen_at",
    ),
    "research_project_observation_plans": (
        "project_id",
        "status",
        "cadence_days",
        "next_review_on",
        "last_reviewed_at",
        "last_review_evidence_fingerprint",
        "last_review_report_fingerprint",
    ),
    "research_project_products": ("project_id", "product_id", "role"),
    "research_project_keywords": ("project_id", "keyword_id", "role"),
    "research_project_notes": ("project_id", "note_type", "content_hash"),
    "market_niches": ("marketplace", "normalized_name", "status"),
    "niche_keywords": ("niche_id", "keyword_id", "role"),
    "niche_products": ("niche_id", "product_id", "role"),
    "research_project_niches": ("project_id", "niche_id", "role"),
    "niche_snapshots": ("niche_id", "snapshot_at", "evidence_hash"),
    "scoring_profiles": (
        "marketplace",
        "normalized_name",
        "status",
        "current_version_id",
    ),
    "scoring_profile_versions": (
        "profile_id",
        "version_no",
        "config_schema_version",
        "scope_type",
        "config_sha256",
    ),
}


class SchemaMigrationError(RuntimeError):
    """Raised when schema migration safety checks or execution fail."""


@dataclass(frozen=True)
class MigrationFile:
    migration_id: str
    filename: str
    path: Path
    checksum: str
    rollback_path: Path | None
    rollback_checksum: str | None


def split_sql(sql: str) -> list[str]:
    """Split this project's line-oriented SQL files into executable statements."""
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).strip().rstrip(";")
            if statement:
                statements.append(statement)
            current = []
    if current:
        statements.append("\n".join(current).strip())
    return statements


def _checksum(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[MigrationFile]:
    if not directory.exists():
        raise SchemaMigrationError(f"未找到数据库迁移目录: {directory}")
    migrations: list[MigrationFile] = []
    for path in sorted(directory.glob("*.sql"), key=lambda item: item.name):
        if path.name.endswith(".down.sql"):
            continue
        rollback = path.with_name(f"{path.stem}.down.sql")
        migrations.append(
            MigrationFile(
                migration_id=path.stem,
                filename=path.name,
                path=path,
                checksum=_checksum(path),
                rollback_path=rollback if rollback.exists() else None,
                rollback_checksum=_checksum(rollback) if rollback.exists() else None,
            )
        )
    return migrations


def inspect_required_schema(cursor: Any, database: str) -> tuple[list[str], dict[str, list[str]]]:
    cursor.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = %s
        """,
        (database,),
    )
    inventory: dict[str, set[str]] = {}
    for row in cursor.fetchall():
        table = str(row.get("table_name") or row.get("TABLE_NAME") or "")
        column = str(row.get("column_name") or row.get("COLUMN_NAME") or "")
        if table and column:
            inventory.setdefault(table, set()).add(column)
    missing_tables = sorted(table for table in REQUIRED_SCHEMA if table not in inventory)
    missing_columns = {
        table: sorted(column for column in columns if column not in inventory.get(table, set()))
        for table, columns in REQUIRED_SCHEMA.items()
        if table in inventory
        and any(column not in inventory.get(table, set()) for column in columns)
    }
    return missing_tables, missing_columns


def checksum_issues(
    records: dict[str, dict[str, Any]], migrations: Iterable[MigrationFile]
) -> tuple[list[str], list[str]]:
    migration_map = {migration.migration_id: migration for migration in migrations}
    changed = sorted(
        migration_id
        for migration_id, record in records.items()
        if record.get("status") == SUCCESS_STATUS
        and migration_id in migration_map
        and str(record.get("checksum") or "") != migration_map[migration_id].checksum
    )
    orphaned = sorted(
        migration_id
        for migration_id, record in records.items()
        if record.get("status") == SUCCESS_STATUS and migration_id not in migration_map
    )
    return changed, orphaned


class MigrationManager:
    def __init__(self, client: Any, migrations_dir: Path = MIGRATIONS_DIR) -> None:
        self.client = client
        self.migrations_dir = migrations_dir

    @staticmethod
    def _ensure_ledger(cursor: Any) -> None:
        cursor.execute(SCHEMA_MIGRATIONS_TABLE_SQL)

    def _records(self, cursor: Any) -> dict[str, dict[str, Any]]:
        cursor.execute("SELECT * FROM schema_migrations ORDER BY migration_id")
        return {str(row["migration_id"]): row for row in cursor.fetchall()}

    def _acquire_lock(self, cursor: Any) -> None:
        lock_name = f"amazon_selection:migrations:{self.client.config.database}"[:64]
        cursor.execute("SELECT GET_LOCK(%s, %s) AS acquired", (lock_name, 30))
        row = cursor.fetchone() or {}
        if int(row.get("acquired") or 0) != 1:
            raise SchemaMigrationError("等待数据库迁移锁超时，可能有另一个初始化进程正在运行")

    def _release_lock(self, cursor: Any) -> None:
        lock_name = f"amazon_selection:migrations:{self.client.config.database}"[:64]
        cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))

    @staticmethod
    def _execute_file(cursor: Any, path: Path) -> None:
        for statement in split_sql(path.read_text(encoding="utf-8")):
            normalized = statement.lstrip().upper()
            if normalized.startswith("CREATE DATABASE ") or normalized.startswith("USE "):
                continue
            cursor.execute(statement)

    @staticmethod
    def _upsert_state(
        cursor: Any,
        migration: MigrationFile,
        *,
        status: str,
        mode: str,
        execution_ms: int | None = None,
        error_message: str | None = None,
        applied: bool = False,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO schema_migrations (
              migration_id, filename, checksum, status, execution_mode,
              rollback_checksum, execution_ms, error_message, applied_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NOW() ELSE NULL END)
            ON DUPLICATE KEY UPDATE
              filename = VALUES(filename),
              checksum = VALUES(checksum),
              status = VALUES(status),
              execution_mode = VALUES(execution_mode),
              rollback_checksum = VALUES(rollback_checksum),
              execution_ms = VALUES(execution_ms),
              error_message = VALUES(error_message),
              applied_at = CASE WHEN %s THEN NOW() ELSE applied_at END
            """,
            (
                migration.migration_id,
                migration.filename,
                migration.checksum,
                status,
                mode,
                migration.rollback_checksum,
                execution_ms,
                error_message,
                applied,
                applied,
            ),
        )

    def _baseline(self, cursor: Any, migrations: list[MigrationFile]) -> None:
        for migration in migrations:
            self._upsert_state(
                cursor,
                migration,
                status=SUCCESS_STATUS,
                mode="baseline",
                execution_ms=0,
                applied=True,
            )

    def _apply_one(self, conn: Any, cursor: Any, migration: MigrationFile) -> None:
        self._upsert_state(cursor, migration, status="running", mode="migration")
        conn.commit()
        started = time.monotonic()
        try:
            self._execute_file(cursor, migration.path)
            elapsed = max(0, round((time.monotonic() - started) * 1000))
            self._upsert_state(
                cursor,
                migration,
                status=SUCCESS_STATUS,
                mode="migration",
                execution_ms=elapsed,
                applied=True,
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            elapsed = max(0, round((time.monotonic() - started) * 1000))
            self._upsert_state(
                cursor,
                migration,
                status="failed",
                mode="migration",
                execution_ms=elapsed,
                error_message=str(exc)[:4000],
            )
            conn.commit()
            raise SchemaMigrationError(
                f"迁移 {migration.filename} 执行失败；DDL 可能已部分生效，请检查台账后修复再重试: {exc}"
            ) from exc

    def initialize(
        self,
        *,
        schema_path: Path,
        legacy_bootstrap: Callable[[Any], None],
    ) -> dict[str, Any]:
        migrations = discover_migrations(self.migrations_dir)
        with self.client.connect() as conn:
            with conn.cursor() as cursor:
                self._acquire_lock(cursor)
                try:
                    self._ensure_ledger(cursor)
                    conn.commit()
                    records = self._records(cursor)
                    successful = {
                        migration_id: record
                        for migration_id, record in records.items()
                        if record.get("status") == SUCCESS_STATUS
                    }
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS total
                        FROM information_schema.tables
                        WHERE table_schema = %s AND table_name = 'products'
                        """,
                        (self.client.config.database,),
                    )
                    has_products = int((cursor.fetchone() or {}).get("total") or 0) > 0

                    if not has_products:
                        self._execute_file(cursor, schema_path)
                        action = "fresh_baseline"
                    elif not successful:
                        # Create any absent current tables, then add only missing
                        # legacy columns through guarded helpers.
                        self._execute_file(cursor, schema_path)
                        legacy_bootstrap(cursor)
                        action = "legacy_baseline"
                    else:
                        changed, orphaned = checksum_issues(records, migrations)
                        if changed or orphaned:
                            details = []
                            if changed:
                                details.append("文件被改写: " + "、".join(changed))
                            if orphaned:
                                details.append("文件缺失: " + "、".join(orphaned))
                            raise SchemaMigrationError("迁移校验失败（" + "；".join(details) + "）")
                        action = "current"

                    records = self._records(cursor)
                    successful = {
                        migration_id
                        for migration_id, record in records.items()
                        if record.get("status") == SUCCESS_STATUS
                    }
                    if action.endswith("baseline"):
                        missing_tables, missing_columns = inspect_required_schema(
                            cursor, self.client.config.database
                        )
                        if missing_tables or missing_columns:
                            raise SchemaMigrationError(
                                _missing_schema_message(missing_tables, missing_columns)
                            )
                        self._baseline(cursor, migrations)
                        conn.commit()
                    else:
                        pending = [m for m in migrations if m.migration_id not in successful]
                        for migration in pending:
                            self._apply_one(conn, cursor, migration)
                        if pending:
                            action = "migrated"

                    missing_tables, missing_columns = inspect_required_schema(
                        cursor, self.client.config.database
                    )
                    if missing_tables or missing_columns:
                        raise SchemaMigrationError(
                            _missing_schema_message(missing_tables, missing_columns)
                        )
                    records = self._records(cursor)
                    return {
                        "action": action,
                        "database": self.client.config.database,
                        "migration_count": len(migrations),
                        "baseline_count": sum(
                            1
                            for row in records.values()
                            if row.get("status") == SUCCESS_STATUS
                            and row.get("execution_mode") == "baseline"
                        ),
                        "applied_count": sum(
                            1
                            for row in records.values()
                            if row.get("status") == SUCCESS_STATUS
                            and row.get("execution_mode") == "migration"
                        ),
                    }
                finally:
                    try:
                        self._release_lock(cursor)
                    except Exception:  # noqa: BLE001 - connection close also releases it.
                        pass

    def rollback(self, migration_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise SchemaMigrationError("回滚必须显式传入 confirmed=True")
        migrations = discover_migrations(self.migrations_dir)
        migration_map = {migration.migration_id: migration for migration in migrations}
        migration = migration_map.get(migration_id)
        if migration is None:
            raise SchemaMigrationError(f"未找到迁移: {migration_id}")
        if migration.rollback_path is None:
            raise SchemaMigrationError(f"迁移 {migration_id} 没有配套 .down.sql，不能自动回滚")

        with self.client.connect() as conn:
            with conn.cursor() as cursor:
                self._acquire_lock(cursor)
                try:
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS total
                        FROM information_schema.tables
                        WHERE table_schema = %s AND table_name = 'schema_migrations'
                        """,
                        (self.client.config.database,),
                    )
                    if int((cursor.fetchone() or {}).get("total") or 0) == 0:
                        raise SchemaMigrationError("数据库尚未建立迁移台账，不能执行回滚")
                    records = self._records(cursor)
                    record = records.get(migration_id)
                    if not record or record.get("status") != SUCCESS_STATUS:
                        raise SchemaMigrationError(f"迁移 {migration_id} 当前不是已应用状态")
                    if record.get("execution_mode") != "migration":
                        raise SchemaMigrationError("历史基线不能自动回滚")
                    later = [
                        item.migration_id
                        for item in migrations
                        if item.migration_id > migration_id
                        and records.get(item.migration_id, {}).get("status") == SUCCESS_STATUS
                        and records.get(item.migration_id, {}).get("execution_mode") == "migration"
                    ]
                    if later:
                        raise SchemaMigrationError("请先回滚更新的迁移: " + "、".join(later))
                    if record.get("checksum") != migration.checksum:
                        raise SchemaMigrationError("正向迁移文件校验和已变化，拒绝回滚")
                    if record.get("rollback_checksum") != migration.rollback_checksum:
                        raise SchemaMigrationError("回滚文件校验和与应用时不一致，拒绝回滚")

                    started = time.monotonic()
                    try:
                        self._execute_file(cursor, migration.rollback_path)
                        elapsed = max(0, round((time.monotonic() - started) * 1000))
                        self._upsert_state(
                            cursor,
                            migration,
                            status="rolled_back",
                            mode="rollback",
                            execution_ms=elapsed,
                        )
                        conn.commit()
                        return {"migration_id": migration_id, "status": "rolled_back"}
                    except Exception as exc:
                        conn.rollback()
                        elapsed = max(0, round((time.monotonic() - started) * 1000))
                        self._upsert_state(
                            cursor,
                            migration,
                            status="failed",
                            mode="rollback",
                            execution_ms=elapsed,
                            error_message=str(exc)[:4000],
                        )
                        conn.commit()
                        raise SchemaMigrationError(
                            f"回滚 {migration_id} 失败；DDL 可能已部分生效，请人工检查: {exc}"
                        ) from exc
                finally:
                    try:
                        self._release_lock(cursor)
                    except Exception:  # noqa: BLE001
                        pass


def _missing_schema_message(
    missing_tables: list[str], missing_columns: dict[str, list[str]]
) -> str:
    parts: list[str] = []
    if missing_tables:
        parts.append("缺少表: " + "、".join(missing_tables))
    if missing_columns:
        rendered = "；".join(
            f"{table}({','.join(columns)})" for table, columns in missing_columns.items()
        )
        parts.append("缺少字段: " + rendered)
    return "数据库结构未达到当前版本要求（" + "；".join(parts) + "）"


def get_database_readiness(
    client: Any | None = None, *, migrations_dir: Path = MIGRATIONS_DIR
) -> dict[str, Any]:
    """Return a read-only database readiness report; never creates or alters schema."""
    try:
        if client is None:
            from database.mysql_client import MySQLClient

            client = MySQLClient()
        migrations = discover_migrations(migrations_dir)
        with client.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS version")
                version = str((cursor.fetchone() or {}).get("version") or "")
                missing_tables, missing_columns = inspect_required_schema(
                    cursor, client.config.database
                )
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = 'schema_migrations'
                    """,
                    (client.config.database,),
                )
                ledger_exists = int((cursor.fetchone() or {}).get("total") or 0) > 0
                records: dict[str, dict[str, Any]] = {}
                if ledger_exists:
                    cursor.execute("SELECT * FROM schema_migrations ORDER BY migration_id")
                    records = {str(row["migration_id"]): row for row in cursor.fetchall()}

        migration_ids = {migration.migration_id for migration in migrations}
        pending = sorted(
            migration_id
            for migration_id in migration_ids
            if records.get(migration_id, {}).get("status") != SUCCESS_STATUS
        )
        failed = sorted(
            migration_id
            for migration_id, row in records.items()
            if row.get("status") in {"failed", "running"}
        )
        changed, orphaned = checksum_issues(records, migrations)
        ready = not any(
            (missing_tables, missing_columns, not ledger_exists, pending, failed, changed, orphaned)
        )
        if ready:
            state = "ready"
            message = "数据库连接、结构与迁移台账均已就绪"
        elif missing_tables or missing_columns:
            state = "schema_incomplete"
            message = _missing_schema_message(missing_tables, missing_columns)
        elif not ledger_exists:
            state = "migration_unmanaged"
            message = "数据库尚未纳入迁移台账，请运行 python scripts/init_mysql.py"
        elif changed or orphaned:
            state = "migration_checksum_error"
            message = "迁移文件与台账不一致，请停止启动并检查迁移历史"
        elif failed:
            state = "migration_failed"
            message = "存在失败或未完成迁移: " + "、".join(failed)
        else:
            state = "migration_pending"
            message = "存在待执行迁移: " + "、".join(pending)
        return {
            "ready": ready,
            "state": state,
            "message": message,
            "database": client.config.database,
            "mysql_version": version,
            "ledger_exists": ledger_exists,
            "business_table_count": len(REQUIRED_SCHEMA) - len(missing_tables),
            "migration_count": len(migrations),
            "baseline_count": sum(
                1
                for row in records.values()
                if row.get("status") == SUCCESS_STATUS and row.get("execution_mode") == "baseline"
            ),
            "applied_count": sum(
                1
                for row in records.values()
                if row.get("status") == SUCCESS_STATUS and row.get("execution_mode") == "migration"
            ),
            "pending": pending,
            "failed": failed,
            "checksum_changed": changed,
            "orphaned": orphaned,
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
        }
    except Exception as exc:  # noqa: BLE001 - readiness must produce a stable report.
        return {
            "ready": False,
            "state": "database_unavailable",
            "message": f"数据库不可用: {exc}",
            "database": getattr(getattr(client, "config", None), "database", None),
            "mysql_version": None,
            "ledger_exists": False,
            "business_table_count": 0,
            "migration_count": 0,
            "baseline_count": 0,
            "applied_count": 0,
            "pending": [],
            "failed": [],
            "checksum_changed": [],
            "orphaned": [],
            "missing_tables": [],
            "missing_columns": {},
        }
