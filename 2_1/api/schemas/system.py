"""Schemas for liveness, readiness, desktop helpers, and settings."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DesktopOpenWebIn(BaseModel):
    path: str = "/"


class DesktopOpenAmazonProductIn(BaseModel):
    asin: str
    marketplace: str = "US"
    product_url: str | None = None


class SettingsPatchIn(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


class LocalDataBackupIn(BaseModel):
    confirmed: bool = False


class DatabaseReadinessData(BaseModel):
    ready: bool
    state: str
    message: str
    database: str | None = None
    mysql_version: str | None = None
    ledger_exists: bool = False
    business_table_count: int = 0
    migration_count: int = 0
    baseline_count: int = 0
    applied_count: int = 0
    pending: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    checksum_changed: list[str] = Field(default_factory=list)
    orphaned: list[str] = Field(default_factory=list)
    missing_tables: list[str] = Field(default_factory=list)
    missing_columns: dict[str, list[str]] = Field(default_factory=dict)


class SettingsUpdateData(BaseModel):
    settings: dict[str, Any]
    changes: list[dict[str, Any]] = Field(default_factory=list)


class SettingsReadData(SettingsUpdateData):
    schema_data: dict[str, Any] = Field(alias="schema")
    defaults: dict[str, Any]


class DeploymentCheckData(BaseModel):
    id: str
    label: str
    status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DeploymentPreflightData(BaseModel):
    checked_at: str
    state: str
    message: str
    ready_for_analysis: bool
    ready_for_collection: bool
    mysql_in_local_backup: bool = False
    browser_session_in_local_backup: bool = False
    checks: list[DeploymentCheckData] = Field(default_factory=list)


class LocalDataBackupData(BaseModel):
    archive_path: str
    archive_sha256: str
    file_count: int
    total_bytes: int
    includes_private_config: bool = False
    mysql_included: bool = False
    message: str
