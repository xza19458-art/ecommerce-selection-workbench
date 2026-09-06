from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import ingestion  # noqa: E402


def test_embedded_snapshot_time_supports_manual_and_tracking_names() -> None:
    manual = Path("html/squishy/20260716_101122/squishy_p1_20260716_101122.html")
    tracking = Path("html/snapshots/20260713_1100/squishy/squishy_p1_20260713_1100.html")

    assert ingestion._embedded_snapshot_time(manual) == datetime(2026, 7, 16, 10, 11, 22)
    assert ingestion._embedded_snapshot_time(tracking) == datetime(2026, 7, 13, 11, 0)


def test_files_without_timestamp_share_parent_batch_mtime(tmp_path: Path) -> None:
    first = tmp_path / "keyword_1.html"
    second = tmp_path / "keyword_2.html"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    os.utime(first, (1_788_000_000, 1_788_000_000))
    os.utime(second, (1_788_000_120, 1_788_000_120))

    result = ingestion._snapshot_times_for_files([first, second], None)

    assert result[first] == result[second]
    assert result[first] == datetime.fromtimestamp(1_788_000_000).replace(microsecond=0)


class _Cursor(AbstractContextManager):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection(AbstractContextManager):
    def __init__(self, client) -> None:
        self.client = client

    def __enter__(self):
        self.client.connection_count += 1
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _Cursor()


class _FailingClient:
    def __init__(self) -> None:
        self.connection_count = 0
        self.finished: list[dict] = []

    def connect(self):
        return _Connection(self)

    def create_job(self, _cursor, _keyword, _url, _pages):
        return 42

    def finish_job(self, _cursor, job_id, status, total_found, total_valid, total_inserted, error_message=None):
        self.finished.append(
            {
                "job_id": job_id,
                "status": status,
                "total_found": total_found,
                "total_valid": total_valid,
                "total_inserted": total_inserted,
                "error_message": error_message,
            }
        )

    def ensure_product_attribute_columns(self, _cursor):
        return None

    def ensure_translation_columns(self, _cursor):
        return None

    def upsert_keyword(self, _cursor, _keyword, _marketplace):
        return None

    def upsert_product(self, _cursor, _record):
        raise RuntimeError("simulated product write failure")


def test_failed_ingestion_finishes_job_in_separate_connection(monkeypatch, tmp_path: Path) -> None:
    client = _FailingClient()
    record = SimpleNamespace(title="Example")
    monkeypatch.setattr(ingestion, "parse_html_files", lambda *_args, **_kwargs: ([record], []))
    monkeypatch.setattr(
        ingestion,
        "load_translation_config",
        lambda: SimpleNamespace(translate_products=False, enabled=False, use_cache=False),
    )
    monkeypatch.setattr(ingestion, "build_translator", lambda _config: object())
    html = tmp_path / "sample_20260716_100000.html"
    html.write_text("<html></html>", encoding="utf-8")

    with pytest.raises(RuntimeError, match="simulated product write failure"):
        ingestion.ingest_html_files_to_mysql([html], client=client)

    assert client.connection_count == 3
    assert client.finished == [
        {
            "job_id": 42,
            "status": "失败",
            "total_found": 1,
            "total_valid": 1,
            "total_inserted": 0,
            "error_message": "simulated product write failure",
        }
    ]
