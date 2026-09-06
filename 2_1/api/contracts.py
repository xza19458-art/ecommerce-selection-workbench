"""Shared HTTP response contract for API routers."""

from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": jsonable_encoder(data), "message": ""}
