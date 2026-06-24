from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.settings import (
    DEFAULT_SETTINGS,
    MAX_PAGES_PER_KEYWORD,
    MIN_PAGE_DELAY_SECONDS,
    MIN_SNAPSHOT_EXPIRE_DAYS,
    MIN_TRACKING_INTERVAL_HOURS,
    SCHEMA_VERSION,
    get_collection_limits,
    get_settings_schema,
    load_settings,
    save_settings,
    update_settings,
)
from services.keyword_tracking import normalize_tracking_pages_per_keyword


def test_missing_settings_file_returns_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "settings.json"
        settings = load_settings(path)

    assert settings == DEFAULT_SETTINGS
    assert get_settings_schema()["properties"]["collection"]["properties"]["max_pages_per_keyword"]["maximum"] == 7


def test_settings_example_matches_code_defaults() -> None:
    example_path = ROOT / "config" / "settings.example.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))

    assert example == DEFAULT_SETTINGS


def test_save_settings_merges_defaults_and_clamps_collection_bounds() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "settings.json"
        result = save_settings(
            {
                "collection": {
                    "page_delay_min_seconds": 1,
                    "page_delay_max_seconds": 2,
                    "pages_per_keyword": 99,
                    "max_pages_per_keyword": 99,
                    "tracking_min_interval_hours": 1,
                    "snapshot_expire_days": 0,
                    "max_runtime_minutes": 9999,
                }
            },
            path,
        )
        saved = json.loads(path.read_text(encoding="utf-8"))

    collection = result.settings["collection"]
    assert saved["schema_version"] == SCHEMA_VERSION
    assert collection["page_delay_min_seconds"] == MIN_PAGE_DELAY_SECONDS
    assert collection["page_delay_max_seconds"] == MIN_PAGE_DELAY_SECONDS
    assert collection["pages_per_keyword"] == MAX_PAGES_PER_KEYWORD
    assert collection["max_pages_per_keyword"] == MAX_PAGES_PER_KEYWORD
    assert collection["tracking_min_interval_hours"] == MIN_TRACKING_INTERVAL_HOURS
    assert collection["snapshot_expire_days"] == MIN_SNAPSHOT_EXPIRE_DAYS
    assert collection["max_runtime_minutes"] == 9999
    assert {change.path for change in result.changes} >= {
        "collection.page_delay_min_seconds",
        "collection.page_delay_max_seconds",
        "collection.pages_per_keyword",
        "collection.max_pages_per_keyword",
        "collection.tracking_min_interval_hours",
        "collection.snapshot_expire_days",
    }


def test_update_settings_deep_merges_and_preserves_safe_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "settings.json"
        save_settings({"ui": {"theme": "dark"}}, path)
        result = update_settings({"collection": {"page_delay_min_seconds": 8}}, path)

    assert result.settings["ui"]["theme"] == "dark"
    assert result.settings["collection"]["page_delay_min_seconds"] == 8
    assert result.settings["collection"]["page_delay_max_seconds"] == 10
    assert result.settings["analytics"] == DEFAULT_SETTINGS["analytics"]


def test_get_collection_limits_returns_clamped_dataclass() -> None:
    limits = get_collection_limits(
        {
            "collection": {
                "page_delay_min_seconds": 6,
                "page_delay_max_seconds": 4,
                "pages_per_keyword": 5,
                "max_pages_per_keyword": 3,
                "tracking_min_interval_hours": 24,
                "snapshot_expire_days": 2,
                "max_runtime_minutes": 1440,
            }
        }
    )

    assert limits.page_delay_min_seconds == 6
    assert limits.page_delay_max_seconds == 6
    assert limits.pages_per_keyword == 3
    assert limits.max_pages_per_keyword == 3
    assert limits.tracking_min_interval_hours == 72
    assert limits.snapshot_expire_days == 2
    assert limits.max_runtime_minutes == 1440
    assert limits.to_dict()["max_runtime_minutes"] == 1440


def test_tracking_pages_use_settings_default_and_maximum() -> None:
    limits = get_collection_limits(
        {
            "collection": {
                "pages_per_keyword": 4,
                "max_pages_per_keyword": 5,
            }
        }
    )

    assert normalize_tracking_pages_per_keyword(None, limits=limits) == 4
    assert normalize_tracking_pages_per_keyword(99, limits=limits) == 5
    assert normalize_tracking_pages_per_keyword(0, limits=limits) == 1


if __name__ == "__main__":
    tests = [
        test_missing_settings_file_returns_defaults,
        test_settings_example_matches_code_defaults,
        test_save_settings_merges_defaults_and_clamps_collection_bounds,
        test_update_settings_deep_merges_and_preserves_safe_defaults,
        test_get_collection_limits_returns_clamped_dataclass,
        test_tracking_pages_use_settings_default_and_maximum,
    ]
    for test in tests:
        test()
    print(f"settings tests passed: {len(tests)}/{len(tests)}")
