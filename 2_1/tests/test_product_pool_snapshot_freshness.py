from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.product_pool import build_snapshot_freshness


def test_snapshot_freshness_marks_recent_snapshot_active() -> None:
    result = build_snapshot_freshness(
        [{"snapshot_at": "2026-06-22 12:00:00"}],
        now=datetime(2026, 6, 23, 12, 0, 0),
        expire_days=3,
    )

    assert result["is_stale"] is False
    assert result["age_days"] == 1.0
    assert result["snapshot_expire_days"] == 3


def test_snapshot_freshness_marks_old_snapshot_stale() -> None:
    result = build_snapshot_freshness(
        [{"snapshot_at": "2026-06-01 12:00:00"}, {"snapshot_at": "2026-06-10 12:00:00"}],
        now=datetime(2026, 6, 23, 12, 0, 0),
        expire_days=7,
    )

    assert result["latest_snapshot_at"] == "2026-06-10 12:00:00"
    assert result["is_stale"] is True
    assert "超过 7 天" in result["message"]


if __name__ == "__main__":
    tests = [
        test_snapshot_freshness_marks_recent_snapshot_active,
        test_snapshot_freshness_marks_old_snapshot_stale,
    ]
    for test in tests:
        test()
    print(f"product_pool snapshot freshness tests passed: {len(tests)}/{len(tests)}")
