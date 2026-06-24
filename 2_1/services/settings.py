"""User settings storage and safety clamps.

Settings are local per-user preferences stored in ``config/settings.json``.
The defaults and schema live in code so backend services can share one source
of truth for safe collection bounds.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "settings.json"
SCHEMA_VERSION = 1

MIN_PAGE_DELAY_SECONDS = 5
MAX_PAGES_PER_KEYWORD = 7
MIN_TRACKING_INTERVAL_HOURS = 72
MIN_SNAPSHOT_EXPIRE_DAYS = 1


DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "ui": {
        "theme": "system",
        "language": "zh-CN",
        "default_page_size": 20,
        "table_density": "comfortable",
        "confirm_before_write": True,
    },
    "collection": {
        "page_delay_min_seconds": 5,
        "page_delay_max_seconds": 10,
        "pages_per_keyword": 2,
        "max_pages_per_keyword": 7,
        "tracking_min_interval_hours": 72,
        "snapshot_expire_days": 7,
        "max_runtime_minutes": 120,
    },
    "analytics": {
        "opportunity_highlight_score": 70,
        "custom_scoring": {
            "enabled": False,
            "weights": {
                "demand": 0.30,
                "competition": 0.25,
                "rating": 0.15,
                "price": 0.15,
                "rank": 0.15,
                "growth": 0.00,
            },
        },
    },
}


SETTINGS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "选品助手用户设置",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "ui", "collection", "analytics"],
    "properties": {
        "schema_version": {
            "type": "integer",
            "const": SCHEMA_VERSION,
            "default": SCHEMA_VERSION,
        },
        "ui": {
            "type": "object",
            "x-layer": "A",
            "description": "纯展示和本地偏好设置。",
            "additionalProperties": False,
            "properties": {
                "theme": {"type": "string", "enum": ["system", "light", "dark"], "default": "system"},
                "language": {"type": "string", "enum": ["zh-CN"], "default": "zh-CN"},
                "default_page_size": {"type": "integer", "minimum": 5, "maximum": 200, "default": 20},
                "table_density": {
                    "type": "string",
                    "enum": ["compact", "comfortable"],
                    "default": "comfortable",
                },
                "confirm_before_write": {"type": "boolean", "default": True},
            },
        },
        "collection": {
            "type": "object",
            "description": "采集相关设置；B 层字段必须由服务端按安全边界校验和调整。",
            "additionalProperties": False,
            "properties": {
                "page_delay_min_seconds": {
                    "type": "integer",
                    "x-layer": "B",
                    "minimum": MIN_PAGE_DELAY_SECONDS,
                    "default": 5,
                    "description": "搜索页之间最短等待秒数，服务端安全下限 5 秒。",
                },
                "page_delay_max_seconds": {
                    "type": "integer",
                    "x-layer": "B",
                    "minimum": MIN_PAGE_DELAY_SECONDS,
                    "default": 10,
                    "description": "搜索页之间最长等待秒数，不得小于最短等待秒数。",
                },
                "pages_per_keyword": {
                    "type": "integer",
                    "x-layer": "B",
                    "minimum": 1,
                    "maximum": MAX_PAGES_PER_KEYWORD,
                    "default": 2,
                    "description": "单关键词默认采集页数，服务端安全上限 7 页。",
                },
                "max_pages_per_keyword": {
                    "type": "integer",
                    "x-layer": "B",
                    "minimum": 1,
                    "maximum": MAX_PAGES_PER_KEYWORD,
                    "default": 7,
                    "description": "单关键词页数上限，服务端安全上限 7 页。",
                },
                "tracking_min_interval_hours": {
                    "type": "integer",
                    "x-layer": "B",
                    "minimum": MIN_TRACKING_INTERVAL_HOURS,
                    "default": 72,
                    "description": "自动追踪同一关键词的最短间隔，服务端安全下限 72 小时。",
                },
                "snapshot_expire_days": {
                    "type": "integer",
                    "x-layer": "B",
                    "minimum": MIN_SNAPSHOT_EXPIRE_DAYS,
                    "default": 7,
                    "description": "快照视为过期的天数，下限 1 天。",
                },
                "max_runtime_minutes": {
                    "type": "integer",
                    "x-layer": "A",
                    "minimum": 1,
                    "default": 120,
                    "description": "单轮最长运行时间，默认 2 小时；不设置服务端上限。",
                },
            },
        },
        "analytics": {
            "type": "object",
            "description": "分析展示和自定义评分设置；标准评分口径固定不变。",
            "additionalProperties": False,
            "properties": {
                "opportunity_highlight_score": {
                    "type": "integer",
                    "x-layer": "A",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 70,
                },
                "custom_scoring": {
                    "type": "object",
                    "x-layer": "C",
                    "description": "自定义评分为独立参考层；不替换标准分，不能与其他用户的自定义分横向比较。",
                    "additionalProperties": False,
                    "properties": {
                        "enabled": {"type": "boolean", "default": False},
                        "weights": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                key: {"type": "number", "minimum": 0, "maximum": 1, "default": value}
                                for key, value in DEFAULT_SETTINGS["analytics"]["custom_scoring"]["weights"].items()
                            },
                        },
                    },
                },
            },
        },
    },
}


class SettingsError(ValueError):
    """Raised when settings JSON cannot be read or normalized."""


@dataclass(frozen=True)
class ClampChange:
    path: str
    original: Any
    clamped: Any
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SettingsResult:
    settings: dict[str, Any]
    changes: tuple[ClampChange, ...] = tuple()

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings": deepcopy(self.settings),
            "changes": [change.to_dict() for change in self.changes],
        }


@dataclass(frozen=True)
class CollectionLimits:
    page_delay_min_seconds: int
    page_delay_max_seconds: int
    pages_per_keyword: int
    max_pages_per_keyword: int
    tracking_min_interval_hours: int
    snapshot_expire_days: int
    max_runtime_minutes: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def get_default_settings() -> dict[str, Any]:
    return deepcopy(DEFAULT_SETTINGS)


def get_settings_schema() -> dict[str, Any]:
    return deepcopy(SETTINGS_SCHEMA)


def load_settings(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    return load_settings_result(path).settings


def load_settings_result(path: str | Path = CONFIG_PATH) -> SettingsResult:
    config_path = Path(path)
    if not config_path.exists():
        return normalize_settings({})
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SettingsError(f"设置 JSON 格式错误: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsError("设置配置必须是 JSON 对象。")
    return normalize_settings(raw)


def save_settings(settings: dict[str, Any], path: str | Path = CONFIG_PATH) -> SettingsResult:
    result = normalize_settings(settings)
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(result.settings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def update_settings(patch: dict[str, Any], path: str | Path = CONFIG_PATH) -> SettingsResult:
    current = load_settings(path)
    merged = _deep_merge(current, patch)
    return save_settings(merged, path)


def normalize_settings(raw: dict[str, Any] | None) -> SettingsResult:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SettingsError("设置配置必须是 JSON 对象。")

    changes: list[ClampChange] = []
    normalized = get_default_settings()
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["ui"] = _normalize_ui(_as_dict(raw.get("ui")), changes)
    normalized["collection"] = _normalize_collection(_as_dict(raw.get("collection")), changes)
    normalized["analytics"] = _normalize_analytics(_as_dict(raw.get("analytics")), changes)
    return SettingsResult(normalized, tuple(changes))


def clamp_collection_settings(collection: dict[str, Any]) -> SettingsResult:
    changes: list[ClampChange] = []
    normalized = _normalize_collection(collection, changes)
    return SettingsResult(normalized, tuple(changes))


def get_collection_limits(
    settings: dict[str, Any] | None = None,
    *,
    path: str | Path = CONFIG_PATH,
) -> CollectionLimits:
    effective = normalize_settings(settings).settings if settings is not None else load_settings(path)
    collection = effective["collection"]
    return CollectionLimits(
        page_delay_min_seconds=collection["page_delay_min_seconds"],
        page_delay_max_seconds=collection["page_delay_max_seconds"],
        pages_per_keyword=collection["pages_per_keyword"],
        max_pages_per_keyword=collection["max_pages_per_keyword"],
        tracking_min_interval_hours=collection["tracking_min_interval_hours"],
        snapshot_expire_days=collection["snapshot_expire_days"],
        max_runtime_minutes=collection["max_runtime_minutes"],
    )


def _normalize_ui(raw: dict[str, Any], changes: list[ClampChange]) -> dict[str, Any]:
    defaults = DEFAULT_SETTINGS["ui"]
    return {
        "theme": _choice(raw.get("theme"), defaults["theme"], {"system", "light", "dark"}, "ui.theme", changes),
        "language": _choice(raw.get("language"), defaults["language"], {"zh-CN"}, "ui.language", changes),
        "default_page_size": _int_range(
            raw.get("default_page_size"),
            defaults["default_page_size"],
            "ui.default_page_size",
            changes,
            minimum=5,
            maximum=200,
        ),
        "table_density": _choice(
            raw.get("table_density"),
            defaults["table_density"],
            {"compact", "comfortable"},
            "ui.table_density",
            changes,
        ),
        "confirm_before_write": _bool_value(
            raw.get("confirm_before_write"),
            defaults["confirm_before_write"],
            "ui.confirm_before_write",
            changes,
        ),
    }


def _normalize_collection(raw: dict[str, Any], changes: list[ClampChange]) -> dict[str, Any]:
    defaults = DEFAULT_SETTINGS["collection"]
    page_delay_min_seconds = _int_min(
        raw.get("page_delay_min_seconds"),
        defaults["page_delay_min_seconds"],
        "collection.page_delay_min_seconds",
        changes,
        minimum=MIN_PAGE_DELAY_SECONDS,
    )
    page_delay_max_seconds = _int_min(
        raw.get("page_delay_max_seconds"),
        defaults["page_delay_max_seconds"],
        "collection.page_delay_max_seconds",
        changes,
        minimum=MIN_PAGE_DELAY_SECONDS,
    )
    if page_delay_max_seconds < page_delay_min_seconds:
        changes.append(
            ClampChange(
                "collection.page_delay_max_seconds",
                page_delay_max_seconds,
                page_delay_min_seconds,
                "最长等待秒数不得小于最短等待秒数。",
            )
        )
        page_delay_max_seconds = page_delay_min_seconds

    max_pages_per_keyword = _int_range(
        raw.get("max_pages_per_keyword"),
        defaults["max_pages_per_keyword"],
        "collection.max_pages_per_keyword",
        changes,
        minimum=1,
        maximum=MAX_PAGES_PER_KEYWORD,
    )
    pages_per_keyword = _int_range(
        raw.get("pages_per_keyword"),
        defaults["pages_per_keyword"],
        "collection.pages_per_keyword",
        changes,
        minimum=1,
        maximum=MAX_PAGES_PER_KEYWORD,
    )
    if pages_per_keyword > max_pages_per_keyword:
        changes.append(
            ClampChange(
                "collection.pages_per_keyword",
                pages_per_keyword,
                max_pages_per_keyword,
                "默认页数不得超过单关键词页数上限。",
            )
        )
        pages_per_keyword = max_pages_per_keyword

    return {
        "page_delay_min_seconds": page_delay_min_seconds,
        "page_delay_max_seconds": page_delay_max_seconds,
        "pages_per_keyword": pages_per_keyword,
        "max_pages_per_keyword": max_pages_per_keyword,
        "tracking_min_interval_hours": _int_min(
            raw.get("tracking_min_interval_hours"),
            defaults["tracking_min_interval_hours"],
            "collection.tracking_min_interval_hours",
            changes,
            minimum=MIN_TRACKING_INTERVAL_HOURS,
        ),
        "snapshot_expire_days": _int_min(
            raw.get("snapshot_expire_days"),
            defaults["snapshot_expire_days"],
            "collection.snapshot_expire_days",
            changes,
            minimum=MIN_SNAPSHOT_EXPIRE_DAYS,
        ),
        "max_runtime_minutes": _positive_int(
            raw.get("max_runtime_minutes"),
            defaults["max_runtime_minutes"],
            "collection.max_runtime_minutes",
            changes,
        ),
    }


def _normalize_analytics(raw: dict[str, Any], changes: list[ClampChange]) -> dict[str, Any]:
    defaults = DEFAULT_SETTINGS["analytics"]
    raw_custom = _as_dict(raw.get("custom_scoring"))
    raw_weights = _as_dict(raw_custom.get("weights"))
    weight_defaults = defaults["custom_scoring"]["weights"]
    return {
        "opportunity_highlight_score": _int_range(
            raw.get("opportunity_highlight_score"),
            defaults["opportunity_highlight_score"],
            "analytics.opportunity_highlight_score",
            changes,
            minimum=0,
            maximum=100,
        ),
        "custom_scoring": {
            "enabled": _bool_value(
                raw_custom.get("enabled"),
                defaults["custom_scoring"]["enabled"],
                "analytics.custom_scoring.enabled",
                changes,
            ),
            "weights": {
                key: _float_range(
                    raw_weights.get(key),
                    default,
                    f"analytics.custom_scoring.weights.{key}",
                    changes,
                    minimum=0.0,
                    maximum=1.0,
                )
                for key, default in weight_defaults.items()
            },
        },
    }


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise SettingsError("设置更新内容必须是 JSON 对象。")
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _choice(
    value: Any,
    default: str,
    allowed: set[str],
    path: str,
    changes: list[ClampChange],
) -> str:
    if value is None:
        return default
    text = str(value)
    if text in allowed:
        return text
    changes.append(ClampChange(path, value, default, "不支持的选项，已回落默认值。"))
    return default


def _bool_value(value: Any, default: bool, path: str, changes: list[ClampChange]) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    changes.append(ClampChange(path, value, default, "必须是布尔值，已回落默认值。"))
    return default


def _positive_int(value: Any, default: int, path: str, changes: list[ClampChange]) -> int:
    if value is None:
        return default
    number = _parse_int(value)
    if number is None or number < 1:
        changes.append(ClampChange(path, value, default, "必须是正整数，已回落默认值。"))
        return default
    return number


def _int_min(
    value: Any,
    default: int,
    path: str,
    changes: list[ClampChange],
    *,
    minimum: int,
) -> int:
    if value is None:
        return default
    number = _parse_int(value)
    if number is None:
        changes.append(ClampChange(path, value, default, "必须是整数，已回落默认值。"))
        return default
    if number < minimum:
        changes.append(ClampChange(path, number, minimum, f"低于服务端安全下限 {minimum}。"))
        return minimum
    return number


def _int_range(
    value: Any,
    default: int,
    path: str,
    changes: list[ClampChange],
    *,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    number = _parse_int(value)
    if number is None:
        changes.append(ClampChange(path, value, default, "必须是整数，已回落默认值。"))
        return default
    clamped = max(minimum, min(number, maximum))
    if clamped != number:
        changes.append(ClampChange(path, number, clamped, f"必须在 {minimum}..{maximum} 范围内。"))
    return clamped


def _float_range(
    value: Any,
    default: float,
    path: str,
    changes: list[ClampChange],
    *,
    minimum: float,
    maximum: float,
) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        changes.append(ClampChange(path, value, default, "必须是数字，已回落默认值。"))
        return default
    clamped = max(minimum, min(number, maximum))
    if clamped != number:
        changes.append(ClampChange(path, number, clamped, f"必须在 {minimum}..{maximum} 范围内。"))
    return clamped


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
