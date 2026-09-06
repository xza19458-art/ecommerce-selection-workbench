"""System infrastructure access shared by application entry points."""

from __future__ import annotations

from typing import Any
import webbrowser

from database.migration_runner import get_database_readiness
from services.deployment_preflight import get_deployment_preflight
from services.amazon_urls import amazon_product_url
from services.settings import (
    get_default_settings,
    get_settings_schema,
    load_settings_result,
    update_settings,
)
from services.user_data_transfer import create_user_data_backup


class SystemRepository:
    def get_readiness(self) -> dict[str, Any]:
        return get_database_readiness()

    def get_settings(self) -> dict[str, Any]:
        result = load_settings_result()
        return {
            "settings": result.settings,
            "changes": [change.to_dict() for change in result.changes],
            "schema": get_settings_schema(),
            "defaults": get_default_settings(),
        }

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        return update_settings(patch).to_dict()

    def get_deployment_preflight(self) -> dict[str, Any]:
        return get_deployment_preflight()

    def backup_local_data(self) -> dict[str, Any]:
        return create_user_data_backup().to_dict()

    def open_local_web(self, *, base_url: str, path: str) -> str:
        normalized_path = str(path or "/").strip().replace("\\", "/")
        if "://" in normalized_path or normalized_path.startswith("//"):
            raise ValueError("只允许打开当前本地应用页面")
        if not normalized_path.startswith("/"):
            normalized_path = "/" + normalized_path

        normalized_base = str(base_url or "").rstrip("/")
        if not (
            normalized_base.startswith("http://127.0.0.1:")
            or normalized_base.startswith("http://localhost:")
        ):
            raise ValueError("只允许打开本地应用页面")
        url = f"{normalized_base}{normalized_path}"
        webbrowser.open(url, new=2)
        return url

    def open_amazon_product(
        self,
        *,
        asin: str,
        marketplace: str,
        product_url: str | None,
    ) -> str:
        url = amazon_product_url(
            asin,
            marketplace=marketplace,
            source_url=product_url,
        )
        webbrowser.open(url, new=2)
        return url
