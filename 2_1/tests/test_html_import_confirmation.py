from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
import importlib
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

api_app = importlib.import_module("api.app")
controller_module = importlib.import_module("core.controller")
from core.controller import AppController  # noqa: E402
from parsers.amazon_search_parser import AmazonProductRecord  # noqa: E402
from services import ingestion  # noqa: E402


def _record(asin: str, snapshot_at: datetime, *, source_file: str = "sample.html") -> AmazonProductRecord:
    return AmazonProductRecord(
        marketplace="US",
        asin=asin,
        title=f"Product {asin}",
        product_url=f"https://www.amazon.com/dp/{asin}",
        image_url="https://example.com/image.jpg",
        price=19.99,
        rating=4.5,
        review_count=120,
        monthly_bought=300,
        is_deal=False,
        is_sponsored=False,
        page_no=1,
        organic_rank=1,
        snapshot_at=snapshot_at,
        source_file=source_file,
    )


def test_preview_token_is_bound_to_file_content_and_candidate_count(monkeypatch, tmp_path: Path) -> None:
    html = tmp_path / "squishy_p1_20260720_190000.html"
    html.write_text("first version", encoding="utf-8")
    snapshot_at = datetime(2026, 7, 20, 19, 0, 0)
    record = _record("B0TEST0001", snapshot_at, source_file=str(html))
    monkeypatch.setattr(ingestion, "parse_html_files", lambda *_args, **_kwargs: ([record], []))

    first = ingestion.prepare_ingestion_batch([html], keyword="squishy")
    ingestion.validate_ingestion_confirmation(first, first.confirmation_token, 1)

    with pytest.raises(ValueError, match="候选数量"):
        ingestion.validate_ingestion_confirmation(first, first.confirmation_token, 2)

    html.write_text("second version", encoding="utf-8")
    second = ingestion.prepare_ingestion_batch([html], keyword="squishy")

    assert second.confirmation_token != first.confirmation_token
    assert second.batch_fingerprint != first.batch_fingerprint
    with pytest.raises(ValueError, match="重新预览"):
        ingestion.validate_ingestion_confirmation(second, first.confirmation_token, 1)


class _Cursor(AbstractContextManager):
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql: str, _params=None) -> None:
        normalized = " ".join(sql.split()).lower()
        if "from products" in normalized:
            self.rows = [{"id": 11, "asin": "B0EXIST01"}]
        elif "from product_snapshots" in normalized:
            self.rows = [{"product_id": 11, "snapshot_at": datetime(2026, 7, 20, 19, 0, 0)}]
        elif "from keywords" in normalized:
            self.rows = [{"id": 7}]
        elif "from keyword_rank_snapshots" in normalized:
            self.rows = [{"product_id": 11, "snapshot_at": datetime(2026, 7, 20, 19, 0, 0)}]
        else:
            raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Connection(AbstractContextManager):
    def __init__(self) -> None:
        self._cursor = _Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


class _ReadOnlyClient:
    def connect(self):
        return _Connection()


def test_impact_estimate_distinguishes_new_and_existing_rows() -> None:
    snapshot_at = datetime(2026, 7, 20, 19, 0, 0)
    records = [
        _record("B0EXIST01", snapshot_at),
        _record("B0NEW0001", snapshot_at),
    ]

    impact = ingestion.estimate_ingestion_impact(
        records,
        keyword="squishy",
        client=_ReadOnlyClient(),
    )

    assert impact == {
        "candidate_records": 2,
        "unique_asins": 2,
        "new_products": 1,
        "existing_products": 1,
        "new_snapshots": 1,
        "existing_snapshots": 1,
        "new_keyword_ranks": 1,
        "existing_keyword_ranks": 1,
        "keyword_status": "复用已入库关键词",
    }


def test_keyword_inference_supports_manual_and_tracking_directories(monkeypatch, tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    monkeypatch.setattr(
        controller_module,
        "user_data_path",
        lambda *parts: data_root.joinpath(*parts),
    )
    controller = AppController()

    manual = data_root / "html" / "squishy" / "20260720_190000" / "squishy_p1.html"
    tracking = data_root / "html" / "snapshots" / "20260720_190000" / "fidget toys" / "fidget_p1.html"
    legacy_tracking = (
        data_root / "html" / "tracking_snapshots" / "20260720_190000" / "stress toys" / "stress_p1.html"
    )

    assert controller._infer_import_keyword([manual]) == "squishy"
    assert controller._infer_import_keyword([tracking]) == "fidget toys"
    assert controller._infer_import_keyword([legacy_tracking]) == "stress toys"
    assert controller._infer_import_keyword([manual, tracking]) is None


def test_html_prefix_is_always_resolved_from_user_data_root(monkeypatch, tmp_path: Path) -> None:
    data_root = tmp_path / "user-data"
    cwd_copy = tmp_path / "html" / "squishy" / "sample.html"
    cwd_copy.parent.mkdir(parents=True)
    cwd_copy.write_text("wrong cwd copy", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        controller_module,
        "user_data_path",
        lambda *parts: data_root.joinpath(*parts),
    )

    resolved = AppController()._resolve_html_file("html/squishy/sample.html")

    assert resolved == data_root / "html" / "squishy" / "sample.html"


def test_html_file_listing_excludes_blocked_evidence_at_any_depth(monkeypatch, tmp_path: Path) -> None:
    html_root = tmp_path / "html"
    valid = html_root / "snapshots" / "20260720_190000" / "squishy" / "squishy_p1.html"
    blocked = html_root / "snapshots" / "_blocked" / "20260720_190000" / "squishy_empty.html"
    detail = html_root / "_details" / "B0TEST0001.html"
    for path in (valid, blocked, detail):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(api_app, "_HTML_IMPORT_DIR", html_root)

    assert api_app._list_html_files() == ["snapshots/20260720_190000/squishy/squishy_p1.html"]


def test_html_import_commit_requires_bound_explicit_confirmation(monkeypatch) -> None:
    client = TestClient(api_app.app)
    allowed_file = "squishy/20260720_190000/squishy_p1.html"
    monkeypatch.setattr(api_app, "_list_html_files", lambda: [allowed_file])
    calls: list[dict] = []

    def commit(files, keyword, *, confirmation_token, expected_valid):
        calls.append(
            {
                "files": files,
                "keyword": keyword,
                "confirmation_token": confirmation_token,
                "expected_valid": expected_valid,
            }
        )
        return {"入库商品数": expected_valid}

    monkeypatch.setattr(api_app._controller, "import_previewed_files_to_database", commit)

    rejected = client.post(
        "/api/import/html/commit",
        json={"files": [allowed_file], "keyword": "squishy"},
    )
    accepted = client.post(
        "/api/import/html/commit",
        json={
            "files": [allowed_file],
            "keyword": "squishy",
            "confirmation_token": "html-import-v1:test-token",
            "expected_valid": 45,
            "confirmed": True,
        },
    )

    assert rejected.status_code == 422
    assert rejected.json()["message"] == "写入数据库前必须由用户明确确认。"
    assert accepted.status_code == 200
    assert accepted.json()["data"]["入库商品数"] == 45
    assert calls == [
        {
            "files": [f"html/{allowed_file}"],
            "keyword": "squishy",
            "confirmation_token": "html-import-v1:test-token",
            "expected_valid": 45,
        }
    ]
