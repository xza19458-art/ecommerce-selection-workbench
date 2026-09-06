"""System liveness, readiness, desktop helpers, and settings routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from api.contracts import ok
from api.schemas.system import (
    DatabaseReadinessData,
    DeploymentPreflightData,
    DesktopOpenAmazonProductIn,
    DesktopOpenWebIn,
    LocalDataBackupData,
    LocalDataBackupIn,
    SettingsReadData,
    SettingsPatchIn,
    SettingsUpdateData,
)
from repositories.system import SystemRepository
from services.user_data_transfer import UserDataTransferError


router = APIRouter(tags=["system"])
_repository = SystemRepository()


@router.get("/api/health")
def health() -> dict[str, Any]:
    return ok({"status": "ok"})


@router.get("/api/ready")
def readiness() -> Any:
    report = DatabaseReadinessData.model_validate(_repository.get_readiness()).model_dump()
    if report["ready"]:
        return ok(report)
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "data": jsonable_encoder(report),
            "message": report["message"],
            "code": report["state"],
        },
    )


@router.post("/api/desktop/open-web")
def desktop_open_web(body: DesktopOpenWebIn, request: Request) -> dict[str, Any]:
    try:
        url = _repository.open_local_web(base_url=str(request.base_url), path=body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok({"url": url})


@router.post("/api/desktop/open-amazon-product")
def desktop_open_amazon_product(body: DesktopOpenAmazonProductIn) -> dict[str, Any]:
    url = _repository.open_amazon_product(
        asin=body.asin,
        marketplace=body.marketplace,
        product_url=body.product_url,
    )
    return ok({"url": url})


@router.get("/api/settings")
def settings_get() -> dict[str, Any]:
    data = SettingsReadData.model_validate(_repository.get_settings()).model_dump(by_alias=True)
    return ok(data)


@router.post("/api/settings")
def settings_update(body: SettingsPatchIn) -> dict[str, Any]:
    data = SettingsUpdateData.model_validate(_repository.update_settings(body.patch)).model_dump()
    return ok(data)


@router.get("/api/system/deployment-preflight")
def deployment_preflight() -> dict[str, Any]:
    data = DeploymentPreflightData.model_validate(
        _repository.get_deployment_preflight()
    ).model_dump()
    return ok(data)


@router.post("/api/system/local-data-backup")
def local_data_backup(body: LocalDataBackupIn) -> dict[str, Any]:
    if not body.confirmed:
        raise HTTPException(status_code=400, detail="请先确认本地证据迁移边界。")
    try:
        result = _repository.backup_local_data()
    except UserDataTransferError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    data = LocalDataBackupData.model_validate(result).model_dump()
    return ok(data)
