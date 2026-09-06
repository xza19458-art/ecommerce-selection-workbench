"""Schemas for the local analytical warehouse API boundary."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WarehouseTableStatus(BaseModel):
    name: str
    source_table: str
    state: str
    rows: int
    source_rows: int
    parquet_path: str


class WarehouseStatusData(BaseModel):
    status: str
    message: str
    last_synced_at: str | None = None
    manifest_path: str
    duckdb_path: str
    parquet_dir: str
    stale_tables: list[str] = Field(default_factory=list)
    missing_tables: list[str] = Field(default_factory=list)
    tables: list[WarehouseTableStatus] = Field(default_factory=list)


class WarehouseSyncData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total_rows: int = Field(alias="总行数")
    duckdb_path: str = Field(alias="DuckDB")
    parquet_dir: str = Field(alias="Parquet")
    manifest_path: str = Field(alias="清单")
    synced_at: datetime = Field(alias="同步时间")
    tables: dict[str, int] = Field(alias="同步表")
