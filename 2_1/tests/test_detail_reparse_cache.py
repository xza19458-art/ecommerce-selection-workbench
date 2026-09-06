from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.app import app  # noqa: E402
from services import detail_reparse  # noqa: E402
from services.detail_reparse import (  # noqa: E402
    analyze_detail_html_files_cached,
    apply_detail_files,
    list_detail_reparse_candidates,
)


ASIN = "B0TEST0001"
api_client = TestClient(app)


def _write_detail(
    root: Path,
    *,
    title: str = "Cache Test A",
    timestamp: str = "20260701_123456",
) -> Path:
    folder = root / ASIN
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ASIN}_{timestamp}_detail.html"
    path.write_text(
        f"""
        <html><body>
          <input id="ASIN" value="{ASIN}" />
          <span id="productTitle">{title}</span>
          <div id="wayfinding-breadcrumbs_feature_div">
            <a>Toys &amp; Games</a><a>Squeeze Toys</a>
          </div>
        </body></html>
        """,
        encoding="utf-8",
    )
    return path


def test_unchanged_file_hits_cache_and_staleness_is_recomputed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "details"
    cache_path = tmp_path / "cache" / "manifest.json"
    path = _write_detail(root)
    original = detail_reparse.analyze_detail_html_file
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(detail_reparse, "analyze_detail_html_file", counted)
    first, first_cache = analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 7, 10),
        stale_days=30,
    )
    second, second_cache = analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 8, 10),
        stale_days=30,
    )

    assert len(calls) == 1
    assert first_cache["miss_count"] == 1
    assert second_cache["hit_count"] == 1
    assert first[0]["stale"] is False
    assert second[0]["stale"] is True
    assert second[0]["_record"].title == "Cache Test A"


def test_content_hash_invalidates_same_size_and_mtime(
    tmp_path: Path,
) -> None:
    root = tmp_path / "details"
    cache_path = tmp_path / "cache.json"
    path = _write_detail(root, title="Cache Test A")
    before = path.stat()
    first, _cache = analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 7, 10),
    )

    original_size = path.stat().st_size
    _write_detail(root, title="Cache Test B")
    assert path.stat().st_size == original_size
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    second, second_cache = analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 7, 10),
    )

    assert first[0]["_record"].title == "Cache Test A"
    assert second[0]["_record"].title == "Cache Test B"
    assert second_cache["hit_count"] == 0
    assert second_cache["miss_count"] == 1


def test_analysis_version_change_invalidates_existing_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "details"
    cache_path = tmp_path / "cache.json"
    path = _write_detail(root)
    analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 7, 10),
    )

    monkeypatch.setattr(detail_reparse, "DETAIL_ANALYSIS_VERSION", "detail-evidence-analysis-v2")
    _items, cache = analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 7, 10),
    )

    assert cache["miss_count"] == 1
    assert cache["invalidated_count"] == 1
    assert cache["analysis_version"] == "detail-evidence-analysis-v2"


def test_corrupt_manifest_is_rebuilt_without_blocking_results(tmp_path: Path) -> None:
    root = tmp_path / "details"
    cache_path = tmp_path / "cache.json"
    path = _write_detail(root)
    cache_path.write_text("{not-json", encoding="utf-8")

    items, cache = analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 7, 10),
    )

    assert items[0]["asin"] == ASIN
    assert cache["miss_count"] == 1
    assert "损坏" in str(cache["warning"])
    assert cache_path.read_text(encoding="utf-8").startswith("{")


def test_force_refresh_reparses_unchanged_file(tmp_path: Path) -> None:
    root = tmp_path / "details"
    cache_path = tmp_path / "cache.json"
    path = _write_detail(root)
    analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 7, 10),
    )

    _items, cache = analyze_detail_html_files_cached(
        [path],
        root=root,
        cache_path=cache_path,
        now=datetime(2026, 7, 10),
        force_refresh=True,
    )

    assert cache["status"] == "rebuilt"
    assert cache["hit_count"] == 0
    assert cache["miss_count"] == 1


def test_candidate_queue_exposes_cache_stats_without_changing_database(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "details"
    cache_path = tmp_path / "cache.json"
    path = _write_detail(root)
    monkeypatch.setattr(detail_reparse, "DETAIL_HTML_ROOT", root)
    monkeypatch.setattr(detail_reparse, "list_detail_html_files", lambda **_kwargs: [path])
    monkeypatch.setattr(
        detail_reparse,
        "_fetch_current_products",
        lambda _db, _asins: {ASIN: {"asin": ASIN}},
    )
    monkeypatch.setattr(
        detail_reparse,
        "_fetch_product_gaps",
        lambda *_args, **_kwargs: ({"product_total": 1}, {"rows": [], "total": 0, "limit": 1, "offset": 0}),
    )

    result = list_detail_reparse_candidates(
        limit=1,
        file_limit=1,
        client=object(),
        now=datetime(2026, 7, 10),
        cache_path=cache_path,
    )

    assert result["summary"]["cache_miss_count"] == 1
    assert result["cache"]["analysis_version"] == detail_reparse.DETAIL_ANALYSIS_VERSION
    manifest = Path(result["cache"]["manifest"])
    assert not manifest.is_absolute()
    assert manifest.name == "cache.json"


def test_candidate_queue_uses_local_page_missing_as_hold_disposition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "details"
    cache_path = tmp_path / "cache.json"
    path = _write_detail(root)
    captured_at = datetime(2026, 7, 1, 12, 34, 56)
    monkeypatch.setattr(detail_reparse, "DETAIL_HTML_ROOT", root)
    monkeypatch.setattr(detail_reparse, "list_detail_html_files", lambda **_kwargs: [path])
    monkeypatch.setattr(
        detail_reparse,
        "_fetch_current_products",
        lambda _db, _asins: {
            ASIN: {
                "asin": ASIN,
                "category_path": "Toys & Games > Squeeze Toys",
                "date_first_available": None,
                "detail_collected_at": captured_at,
                "detail_source_file": str(path),
            }
        },
    )
    monkeypatch.setattr(
        detail_reparse,
        "_fetch_product_gaps",
        lambda *_args, **_kwargs: (
            {"product_total": 1},
            {
                "rows": [
                    {
                        "asin": ASIN,
                        "evidence_status": "partial",
                        "reasons": ["缺首次可售日期"],
                        "detail_collected_at": captured_at,
                    }
                ],
                "total": 1,
                "limit": 1,
                "offset": 0,
            },
        ),
    )

    result = list_detail_reparse_candidates(
        limit=1,
        file_limit=1,
        client=object(),
        now=datetime(2026, 7, 10),
        cache_path=cache_path,
    )

    row = result["gaps"]["rows"][0]
    assert row["local_file_status"] == "page_missing"
    assert row["recommended_action"]["code"] == "page_missing"
    assert row["recommended_action"]["accesses_amazon"] is False
    assert result["gaps"]["disposition_summary"]["hold_total"] == 1
    assert result["gaps"]["disposition_policy"]["missing_is_zero"] is False


def test_apply_path_still_forces_direct_reparse(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "details"
    path = _write_detail(root)
    parsed = detail_reparse.analyze_detail_html_file(path, root=root, now=datetime(2026, 7, 10))
    calls = []
    persisted = []

    monkeypatch.setattr(detail_reparse, "resolve_detail_html_paths", lambda _paths: [path])

    def direct_analyze(selected, **_kwargs):
        calls.append(selected)
        return dict(parsed)

    monkeypatch.setattr(detail_reparse, "analyze_detail_html_file", direct_analyze)
    monkeypatch.setattr(
        detail_reparse,
        "_fetch_current_products",
        lambda _db, _asins: {ASIN: {"asin": ASIN}},
    )
    monkeypatch.setattr(detail_reparse, "persist_detail_record", lambda *args, **kwargs: persisted.append((args, kwargs)))
    monkeypatch.setattr(
        detail_reparse,
        "analyze_detail_html_files_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("apply must bypass cache")),
    )

    result = apply_detail_files(["ignored"], client=object(), now=datetime(2026, 7, 10))

    assert result["applied"] == 1
    assert calls == [path]
    assert len(persisted) == 1


def test_evidence_api_forwards_explicit_cache_refresh(monkeypatch) -> None:
    calls = []

    def fake_list(**kwargs):
        calls.append(kwargs)
        return {"summary": {}, "cache": {}, "files": [], "gaps": {"rows": [], "total": 0}}

    monkeypatch.setattr(detail_reparse, "list_detail_reparse_candidates", fake_list)
    response = api_client.get(
        "/api/metrics/evidence",
        params={
            "limit": 2,
            "file_limit": 3,
            "stale_days": 45,
            "refresh_cache": "true",
            "priority": "project",
        },
    )

    assert response.status_code == 200
    assert calls == [
        {
            "limit": 2,
            "offset": 0,
            "file_limit": 3,
            "stale_days": 45,
            "force_refresh": True,
            "priority": "project",
        }
    ]
