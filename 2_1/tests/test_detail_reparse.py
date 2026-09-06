from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.detail_reparse import (
    DetailReparseError,
    _attach_current,
    _equivalent,
    analyze_detail_html_file,
    resolve_detail_html_paths,
)


ASIN = "B0TEST0001"


def _write_detail(root: Path, html: str, *, timestamp: str = "20260701_123456") -> Path:
    folder = root / ASIN
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ASIN}_{timestamp}_detail.html"
    path.write_text(html, encoding="utf-8")
    return path


def _valid_html(extra: str = "") -> str:
    return f"""
    <html><body>
      <input id="ASIN" value="{ASIN}" />
      <span id="productTitle">Offline replay test product</span>
      <div id="wayfinding-breadcrumbs_feature_div"><a>Toys &amp; Games</a><a>Squeeze Toys</a></div>
      {extra}
    </body></html>
    """


def test_semantic_marker_without_parsed_value_is_parser_unrecognized() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        path = _write_detail(root, _valid_html("<div>Date First Available: unknown</div>"))

        item = analyze_detail_html_file(path, root=root, now=datetime(2026, 7, 10))

        assert item["coverage"]["date_first_available"]["status"] == "parser_unrecognized"
        assert item["status"] == "parser_unrecognized"
        assert item["captured_at"] == "2026-07-01 12:34:56"


def test_absent_semantic_marker_is_page_missing() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        path = _write_detail(root, _valid_html())

        item = analyze_detail_html_file(path, root=root, now=datetime(2026, 7, 10))

        assert item["coverage"]["date_first_available"]["status"] == "page_missing"
        assert item["status"] == "page_missing"


def test_blocked_page_cannot_be_replayed() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        path = _write_detail(
            root,
            "<html><head><title>Robot Check</title></head><body>Sorry, we just need to make sure you're not a robot</body></html>",
        )

        item = analyze_detail_html_file(path, root=root, now=datetime(2026, 7, 10))

        assert item["status"] == "invalid"
        assert item["can_apply"] is False
        assert "机器人" in str(item["reason"])


def test_selected_paths_must_come_from_scanned_root() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        path = _write_detail(root, _valid_html())
        source_name = analyze_detail_html_file(path, root=root)["path"]

        assert resolve_detail_html_paths([source_name], root=root) == [path]
        try:
            resolve_detail_html_paths(["../outside.html"], root=root)
        except DetailReparseError:
            pass
        else:
            raise AssertionError("path traversal must be rejected")


def test_older_file_marks_current_value_as_preserved() -> None:
    with TemporaryDirectory() as temp:
        root = Path(temp)
        path = _write_detail(root, _valid_html(), timestamp="20260701_123456")
        item = analyze_detail_html_file(path, root=root, now=datetime(2026, 7, 10))
        current = {
            "asin": ASIN,
            "detail_collected_at": datetime(2026, 7, 8, 12, 0, 0),
            "detail_source_file": "html/_details/newer.html",
            "category_path": "A newer and different category path",
            "date_first_available": None,
            "bsr_snapshot_count": 0,
            "latest_offer_price": None,
            "variant_count": 0,
        }

        attached = _attach_current(item, current)
        category_change = next(change for change in attached["changes"] if change["field"] == "category_path")

        assert category_change["action"] == "preserve_newer"


def test_equivalent_specs_ignore_mysql_numeric_types() -> None:
    current = {"item_weight_oz": Decimal("1.411"), "model_number": "1329"}
    parsed = {"item_weight_oz": 1.411, "model_number": "1329"}

    assert _equivalent(current, parsed)


if __name__ == "__main__":
    tests = [
        test_semantic_marker_without_parsed_value_is_parser_unrecognized,
        test_absent_semantic_marker_is_page_missing,
        test_blocked_page_cannot_be_replayed,
        test_selected_paths_must_come_from_scanned_root,
        test_older_file_marks_current_value_as_preserved,
        test_equivalent_specs_ignore_mysql_numeric_types,
    ]
    for test in tests:
        test()
    print(f"detail reparse tests passed: {len(tests)}/{len(tests)}")
