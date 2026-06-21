from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.analytics_warehouse import (  # noqa: E402
    WarehouseConfig,
    list_warehouse_tables,
    sync_analytics_warehouse,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync MySQL analysis tables into a local DuckDB/Parquet warehouse."
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Optional warehouse config path. Defaults to config/warehouse.json when present.",
    )
    parser.add_argument(
        "--root-dir",
        default=None,
        help="Override warehouse root directory. Relative paths are resolved from the project root.",
    )
    parser.add_argument(
        "--parquet-dir",
        default=None,
        help="Override Parquet output directory. Relative paths are resolved from the project root.",
    )
    parser.add_argument(
        "--duckdb-path",
        default=None,
        help="Override DuckDB database path. Relative paths are resolved from the project root.",
    )
    parser.add_argument(
        "--table",
        action="append",
        choices=list_warehouse_tables(),
        help="Warehouse table to sync. Repeat to sync multiple tables. Defaults to all tables.",
    )
    parser.add_argument(
        "--list-tables",
        action="store_true",
        help="Print available warehouse tables and exit.",
    )
    args = parser.parse_args()

    if args.list_tables:
        for table_name in list_warehouse_tables():
            print(table_name)
        return 0

    config = WarehouseConfig.from_file(args.config) if args.config else WarehouseConfig.from_file()
    config = config.with_overrides(
        root_dir=args.root_dir,
        parquet_dir=args.parquet_dir,
        duckdb_path=args.duckdb_path,
    )

    summary = sync_analytics_warehouse(config=config, tables=args.table)
    print("Analytics warehouse sync complete.")
    print(f"DuckDB: {summary.duckdb_path}")
    print(f"Parquet: {summary.parquet_dir}")
    print(f"Total rows: {summary.total_rows}")
    for table in summary.tables:
        print(f"- {table.name}: {table.rows} rows -> {table.parquet_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
