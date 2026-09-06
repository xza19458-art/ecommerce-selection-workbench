"""Analytical warehouse status and explicit synchronization routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from api.contracts import ok
from api.schemas.warehouse import WarehouseStatusData, WarehouseSyncData
from repositories.warehouse import WarehouseRepository


router = APIRouter(prefix="/api/warehouse", tags=["warehouse"])
_repository = WarehouseRepository()


@router.get("/status")
def warehouse_status() -> dict[str, Any]:
    data = WarehouseStatusData.model_validate(_repository.get_status()).model_dump()
    return ok(data)


@router.post("/sync")
def warehouse_sync() -> dict[str, Any]:
    data = WarehouseSyncData.model_validate(_repository.sync()).model_dump(by_alias=True)
    return ok(data)
